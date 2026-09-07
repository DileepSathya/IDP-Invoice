"""Invoice file lifecycle folders: to_be_processed → Completed / HITL_pending / ERROR."""

from __future__ import annotations

import logging
import os
import shutil
from datetime import datetime
from pathlib import Path

from backend.app_paths import app_dir, load_app_dotenv, resolve_data_path

load_app_dotenv()
logger = logging.getLogger(__name__)


def _invoice_dir(env_key: str, default: str, legacy_env_key: str | None = None) -> Path:
    """Resolve folder path; honour legacy env only when the new key is unset."""
    effective_default = default
    if not os.environ.get(env_key, "").strip() and legacy_env_key:
        legacy = os.environ.get(legacy_env_key, "").strip()
        if legacy:
            effective_default = legacy
    return resolve_data_path(env_key, effective_default)


# New layout (all under invoices_data/). Legacy RAW_DIR / UPLOADS_DIR / ERROR_FILES_DIR still work.
TO_BE_PROCESSED_DIR = _invoice_dir(
    "TO_BE_PROCESSED_DIR", "./invoices_data/to_be_processed", "RAW_DIR"
)
HITL_PENDING_DIR = _invoice_dir("HITL_PENDING_DIR", "./invoices_data/HITL_pending")
ERROR_DIR = _invoice_dir("ERROR_DIR", "./invoices_data/ERROR", "ERROR_FILES_DIR")
GEMINI_API_ERROR_DIR = _invoice_dir(
    "GEMINI_API_ERROR_DIR", "./invoices_data/gemini_api_error"
)
COMPLETED_DIR = _invoice_dir("COMPLETED_DIR", "./invoices_data/Completed", "UPLOADS_DIR")
# Merged duplicate pages are moved here so HITL_pending folder counts stay accurate.
MERGED_SOURCES_DIR = _invoice_dir("MERGED_SOURCES_DIR", "./invoices_data/merged_sources")
# UI / API uploads only — not watched by the folder watcher (avoids duplicate OCR).
API_STAGING_DIR = _invoice_dir("API_STAGING_DIR", "./invoices_data/_api_staging")

# Legacy folders (static file lookup only)
_LEGACY_SEARCH_DIRS: tuple[Path, ...] = (
    app_dir() / "invoices_data" / "uploads",
    app_dir() / "raw",
    app_dir() / "invoices_data" / "error_files",
)

INVOICE_FILE_DIRS: tuple[Path, ...] = (
    TO_BE_PROCESSED_DIR,
    HITL_PENDING_DIR,
    MERGED_SOURCES_DIR,
    ERROR_DIR,
    GEMINI_API_ERROR_DIR,
    COMPLETED_DIR,
)

_LAYOUT_DIRS: tuple[Path, ...] = (*INVOICE_FILE_DIRS, API_STAGING_DIR)


def ensure_invoice_data_layout() -> None:
    for folder in _LAYOUT_DIRS:
        folder.mkdir(parents=True, exist_ok=True)


def timestamped_filename(stem: str, ext: str) -> str:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = ext.lower() if ext.startswith(".") else f".{ext.lower()}"
    return f"{stem}_{ts}{suffix}"


def staging_path_for_upload(filename: str) -> Path:
    """Private staging path for UI/API uploads (not watched by the folder watcher)."""
    ensure_invoice_data_layout()
    path = Path(filename)
    stem = path.stem or "file"
    ext = path.suffix or ""
    return (API_STAGING_DIR / timestamped_filename(stem, ext)).resolve()


def destination_dir(*, file_status: str, status: int, pipeline_failed: bool) -> Path:
    if pipeline_failed or file_status == "error":
        return ERROR_DIR
    if status == 1:
        return HITL_PENDING_DIR
    return COMPLETED_DIR


def move_to_gemini_api_error(source: Path) -> Path:
    """Move a file that failed due to Gemini API errors into the recovery folder."""
    return move_invoice_file(source, GEMINI_API_ERROR_DIR)


def list_gemini_api_error_files() -> list[Path]:
    ensure_invoice_data_layout()
    from backend.agents.ocr import ALLOWED_EXTS

    files: list[Path] = []
    for entry in GEMINI_API_ERROR_DIR.iterdir():
        if entry.is_file() and entry.suffix.lower() in ALLOWED_EXTS:
            files.append(entry)
    return sorted(files, key=lambda p: p.name.lower())


def move_invoice_file(source: Path, dest_dir: Path) -> Path:
    """Move file into dest_dir (same basename). Returns final path."""
    ensure_invoice_data_layout()
    source = source.resolve()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = (dest_dir / source.name).resolve()
    if source == dest:
        return dest
    if dest.exists():
        stem = dest.stem
        ext = dest.suffix
        dest = (dest_dir / timestamped_filename(stem, ext)).resolve()
    shutil.move(str(source), str(dest))
    logger.info("[Invoice files] Moved %s → %s", source, dest)
    return dest


def finalize_invoice_file(
    source: Path,
    *,
    file_status: str,
    status: int,
    pipeline_failed: bool = False,
) -> Path:
    dest_dir = destination_dir(
        file_status=file_status,
        status=status,
        pipeline_failed=pipeline_failed,
    )
    return move_invoice_file(source, dest_dir)


def move_hitl_pending_to_completed(file_path: str | Path) -> Path | None:
    path = Path(file_path).resolve()
    if not path.is_file():
        return None
    try:
        if path.parent.resolve() != HITL_PENDING_DIR.resolve():
            return path
    except OSError:
        return path
    return move_invoice_file(path, COMPLETED_DIR)


def resolve_invoice_file(name: str) -> Path | None:
    """Resolve preview/download path by basename (or absolute path if it exists)."""
    if not name or name in {".", ".."}:
        return None

    candidate = Path(name)
    if candidate.is_absolute() and candidate.is_file():
        return candidate.resolve()

    safe_name = Path(name).name
    if not safe_name:
        return None

    for folder in (*INVOICE_FILE_DIRS, *_LEGACY_SEARCH_DIRS):
        path = (folder / safe_name).resolve()
        if path.is_file():
            return path
    return None


def relocate_after_hitl_processed(uploaded_file_path: str | None) -> str | None:
    """Move HITL_pending → Completed after human review. Returns updated path."""
    if not uploaded_file_path:
        return None
    new_path = move_hitl_pending_to_completed(uploaded_file_path)
    if new_path is None:
        return uploaded_file_path
    return str(new_path)


def archive_merged_source_file(file_path: str | Path | None) -> str | None:
    """Move a merged-away invoice file out of HITL_pending into merged_sources."""
    if not file_path:
        return None
    path = Path(file_path)
    if not path.is_file():
        return str(path) if str(path).strip() else None
    MERGED_SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    return str(move_invoice_file(path, MERGED_SOURCES_DIR))
