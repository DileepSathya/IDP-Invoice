"""Live pipeline queue/status from invoice folders, watcher state, and MongoDB."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agents.database import get_jobs_collection
from backend.agents.ocr import ALLOWED_EXTS
from backend.app_paths import logs_dir
from backend.invoice_files import (
    API_STAGING_DIR,
    COMPLETED_DIR,
    ERROR_DIR,
    GEMINI_API_ERROR_DIR,
    HITL_PENDING_DIR,
    TO_BE_PROCESSED_DIR,
)

logger = logging.getLogger(__name__)

_PIPELINE_ACTIVE_FILENAME = "pipeline_active.json"


def pipeline_active_path() -> Path:
    return logs_dir() / _PIPELINE_ACTIVE_FILENAME


def set_pipeline_active(source: str, file_path: Path) -> None:
    """Mark a file as actively being processed (folder watcher or API upload)."""
    path = pipeline_active_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": str(source).strip(),
        "file_name": file_path.name,
        "file_path": str(file_path.resolve()),
        "started_at": datetime.utcnow().isoformat() + "Z",
    }
    path.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")


def clear_pipeline_active() -> None:
    path = pipeline_active_path()
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        logger.debug("Could not remove pipeline active state: %s", exc)


def read_pipeline_active() -> dict[str, Any] | None:
    path = pipeline_active_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def count_invoice_files(folder: Path) -> int:
    if not folder.is_dir():
        return 0
    count = 0
    for entry in folder.iterdir():
        if entry.is_file() and entry.suffix.lower() in ALLOWED_EXTS:
            count += 1
    return count


def _watcher_is_active() -> bool:
    active = read_pipeline_active()
    if not active:
        return False
    if str(active.get("source", "")).strip() != "watch_raw":
        return False
    file_path = str(active.get("file_path", "")).strip()
    if file_path and Path(file_path).is_file():
        return True
    file_name = str(active.get("file_name", "")).strip()
    if file_name:
        candidate = (TO_BE_PROCESSED_DIR / file_name).resolve()
        if candidate.is_file():
            return True
    clear_pipeline_active()
    return False


def _count_async_jobs() -> int:
    try:
        jobs = get_jobs_collection()
        return int(
            jobs.count_documents({"status": {"$in": ["queued", "processing"]}})
        )
    except Exception as exc:
        logger.debug("Could not count async invoice jobs: %s", exc)
        return 0


def collect_pipeline_status(*, overview: dict[str, Any]) -> dict[str, Any]:
    queue_total = count_invoice_files(TO_BE_PROCESSED_DIR)
    api_staging = count_invoice_files(API_STAGING_DIR)
    watcher_active = _watcher_is_active()
    async_jobs = _count_async_jobs()

    awaiting = max(0, queue_total - (1 if watcher_active else 0))
    in_process = api_staging + (1 if watcher_active else 0) + async_jobs

    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "awaiting_processing": awaiting,
        "in_process": in_process,
        "processed": count_invoice_files(COMPLETED_DIR),
        "error": count_invoice_files(ERROR_DIR),
        "gemini_api_error": count_invoice_files(GEMINI_API_ERROR_DIR),
        "hitl_pending": count_invoice_files(HITL_PENDING_DIR),
        "queue_total": queue_total,
        "api_staging": api_staging,
        "watcher_active": watcher_active,
        "async_jobs": async_jobs,
        "stored_total": int(overview.get("total_uploaded_files", 0)),
        "stored_healthy": int(overview.get("healthy_files", 0)),
        "stored_errors": int(overview.get("error_files", 0)),
        "hitl_flagged_total": int(overview.get("hitl_flagged_files", 0)),
        "hitl_review_pending": int(overview.get("hitl_process_pending", 0)),
        "hitl_reviewed": int(overview.get("hitl_processed", 0)),
        "system_processed": int(overview.get("system_processed", 0)),
        "human_approved_files": int(overview.get("human_approved_files", 0)),
        "gemini_quota_error_count": int(overview.get("gemini_quota_error_count", 0)),
        "network_error_count": int(overview.get("network_error_count", 0)),
    }
