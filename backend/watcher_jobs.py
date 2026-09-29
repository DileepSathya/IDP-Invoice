"""Link API v1 async jobs to files processed by the folder watcher."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from bson import ObjectId

from backend.agents.database import get_invoices_collection, get_jobs_collection

logger = logging.getLogger(__name__)


def _normalize_queue_path(path: Path | str) -> str:
    return str(Path(path).resolve())


def find_job_for_queue_path(path: Path | str) -> Optional[dict[str, Any]]:
    jobs = get_jobs_collection()
    qpath = _normalize_queue_path(path)
    doc = jobs.find_one(
        {"queue_path": qpath, "status": {"$in": ["queued", "processing"]}},
    )
    if doc:
        return doc
    doc = jobs.find_one(
        {"uploaded_file_path": qpath, "status": {"$in": ["queued", "processing"]}},
    )
    if doc:
        return doc
    name = Path(qpath).name
    if not name:
        return None
    return jobs.find_one(
        {"queue_file_name": name, "status": {"$in": ["queued", "processing"]}},
    )


def on_watcher_processing_started(path: Path) -> Optional[str]:
    """Mark v1 job as processing. Returns job_id when matched."""
    job = find_job_for_queue_path(path)
    if not job:
        return None
    job_id = str(job.get("job_id") or "")
    tenant_id = str(job.get("tenant_id") or "")
    if not job_id or not tenant_id:
        return None
    started_at = datetime.utcnow()
    get_jobs_collection().update_one(
        {"job_id": job_id, "tenant_id": tenant_id},
        {"$set": {"status": "processing", "started_at": started_at}},
    )
    logger.info(
        "job_started tenant_id=%s job_id=%s queue_path=%s",
        tenant_id,
        job_id,
        _normalize_queue_path(path),
    )
    return job_id


def on_watcher_processing_completed(
    queue_path: str,
    *,
    inserted_id: str,
    file_status: str,
) -> None:
    job = find_job_for_queue_path(queue_path)
    if not job:
        return
    job_id = str(job.get("job_id") or "")
    tenant_id = str(job.get("tenant_id") or "")
    request_id = job.get("request_id")
    completed_at = datetime.utcnow()
    get_jobs_collection().update_one(
        {"job_id": job_id, "tenant_id": tenant_id},
        {
            "$set": {
                "status": "completed",
                "invoice_id": inserted_id,
                "file_status": file_status,
                "completed_at": completed_at,
            }
        },
    )
    try:
        get_invoices_collection().update_one(
            {"_id": ObjectId(inserted_id)},
            {"$set": {"tenant_id": tenant_id, "job_id": job_id}},
        )
    except Exception as exc:
        logger.warning("Could not attach tenant/job to invoice %s: %s", inserted_id, exc)
    logger.info(
        "job_completed tenant_id=%s job_id=%s invoice_id=%s",
        tenant_id,
        job_id,
        inserted_id,
    )
    from backend.api import _send_job_webhook_if_enabled

    _send_job_webhook_if_enabled(
        job_id=job_id,
        tenant_id=tenant_id,
        event="job.completed",
        request_id=request_id,
        invoice_id=inserted_id,
        file_status=file_status,
        error_message=None,
    )


def on_watcher_processing_failed(queue_path: str, error_message: str) -> None:
    job = find_job_for_queue_path(queue_path)
    if not job:
        return
    job_id = str(job.get("job_id") or "")
    tenant_id = str(job.get("tenant_id") or "")
    request_id = job.get("request_id")
    get_jobs_collection().update_one(
        {"job_id": job_id, "tenant_id": tenant_id},
        {
            "$set": {
                "status": "failed",
                "error": error_message,
                "completed_at": datetime.utcnow(),
            }
        },
    )
    logger.warning(
        "job_failed tenant_id=%s job_id=%s error=%s",
        tenant_id,
        job_id,
        error_message,
    )
    from backend.api import _send_job_webhook_if_enabled

    _send_job_webhook_if_enabled(
        job_id=job_id,
        tenant_id=tenant_id,
        event="job.failed",
        request_id=request_id,
        invoice_id=None,
        file_status=None,
        error_message=error_message,
    )
