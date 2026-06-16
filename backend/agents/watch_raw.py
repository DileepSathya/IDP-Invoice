from __future__ import annotations

import logging
import os
import queue
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Allow running as: `python backend/agents/watch_raw.py` from repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.agents.ocr import ALLOWED_EXTS, process_file  # noqa: E402
from backend.app_logging import configure_logging  # noqa: E402
from backend.app_paths import load_app_dotenv  # noqa: E402
from backend.hitl_status import pipeline_lifecycle_status  # noqa: E402
from backend.invoice_files import (  # noqa: E402
    TO_BE_PROCESSED_DIR,
    ensure_invoice_data_layout,
    finalize_invoice_file,
)


load_app_dotenv()
logger = logging.getLogger(__name__)


def _is_allowed(path: Path) -> bool:
    if not path.is_file() or path.suffix.lower() not in ALLOWED_EXTS:
        return False
    # Legacy preprocess wrote "*__sharp.*" next to the drop file and re-fired the watcher.
    if "__sharp" in path.stem:
        return False
    return True


def _wait_for_complete_write(path: Path, timeout_s: float = 90.0, poll_s: float = 0.5) -> None:
    start = time.time()
    last_size = -1
    stable_for = 0.0

    while True:
        if not path.exists():
            raise FileNotFoundError(str(path))

        try:
            size = path.stat().st_size
        except OSError:
            size = -2

        if size == last_size and size >= 0:
            stable_for += poll_s
        else:
            stable_for = 0.0
            last_size = size

        if stable_for >= 1.5:
            try:
                with path.open("rb"):
                    return
            except OSError:
                pass

        if time.time() - start > timeout_s:
            raise TimeoutError(f"File never became stable: {path}")

        time.sleep(poll_s)


class _EnqueueHandler(FileSystemEventHandler):
    def __init__(self, q: "queue.Queue[Path]") -> None:
        super().__init__()
        self.q = q

    def on_created(self, event):
        if event.is_directory:
            return
        p = Path(event.src_path)
        if _is_allowed(p):
            logger.info(
                "[Folder watcher] New file in to_be_processed; queued after copy completes: %s",
                p,
            )
            self.q.put(p)

    def on_moved(self, event):
        if event.is_directory:
            return
        p = Path(event.dest_path)
        if _is_allowed(p):
            logger.info(
                "[Folder watcher] File moved into to_be_processed; queued: %s",
                p,
            )
            self.q.put(p)


def _worker(q: "queue.Queue[Path]") -> None:
    while True:
        path = q.get()
        try:
            configure_logging()
            if not path.is_file():
                logger.info(
                    "[Folder watcher → worker] Skipping %s — file no longer present "
                    "(likely moved or removed by another process).",
                    path,
                )
                continue
            logger.info(
                "[Folder watcher → worker] Waiting for stable file before OCR: %s",
                path,
            )
            _wait_for_complete_write(path)
            if not path.is_file():
                logger.info(
                    "[Folder watcher → worker] Skipping %s — removed before OCR could start.",
                    path,
                )
                continue
            try:
                from license_validator import InvoiceQuotaExceeded, ensure_invoice_quota_available

                ensure_invoice_quota_available()
            except InvoiceQuotaExceeded as exc:
                logger.error(
                    "[Folder watcher → worker] %s Skipping file: %s",
                    exc.message,
                    path,
                )
                if path.is_file():
                    try:
                        finalize_invoice_file(
                            path,
                            file_status="error",
                            status=0,
                            pipeline_failed=True,
                        )
                    except Exception:
                        pass
                continue

            logger.info(
                "[Folder watcher → worker] Starting OCR + Gemini pipeline: %s",
                path,
            )
            r = process_file(str(path))

            gemini_json = r.gemini_json or {}
            if not isinstance(gemini_json, dict):
                gemini_json = {}

            invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
            invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
            file_status = "error" if not invoice_number_norm else "healthy file"

            file_received_time = datetime.utcnow()
            try:
                file_received_time = datetime.utcfromtimestamp(path.stat().st_ctime)
            except Exception:
                pass

            if not path.is_file():
                logger.info(
                    "[Folder watcher → worker] Skipping finalize for %s — file already moved.",
                    path,
                )
                continue

            hitl_value, status_value = pipeline_lifecycle_status(gemini_json, file_status=file_status)
            try:
                final_path = finalize_invoice_file(
                    path,
                    file_status=file_status,
                    status=status_value,
                    pipeline_failed=False,
                )
            except FileNotFoundError:
                logger.info(
                    "[Folder watcher → worker] File vanished before move (%s) — "
                    "another process likely finished it.",
                    path,
                )
                continue

            try:
                from backend.agents.database import (
                    get_invoices_collection,
                    record_pipeline_telemetry,
                    store_invoice_result,
                )

                logger.info(
                    "[Folder watcher → MongoDB] Storing extraction (collection from MONGO_INVOICES_COLLECTION).",
                )
                inserted_id = store_invoice_result(
                    file_path=str(final_path),
                    uploaded_file_path=str(final_path),
                    ocr_text=r.ocr_text,
                    gemini_model=r.gemini_model or (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"),
                    gemini_json=gemini_json,
                    gemini_raw_text=r.gemini_raw_text,
                    file_status=file_status,
                )
                logger.info(
                    "[Folder watcher → MongoDB] Insert completed. Document id: %s | file=%s",
                    inserted_id,
                    final_path,
                )
                db_insert_time = datetime.utcnow()
                file_size = 0
                try:
                    file_size = int(final_path.stat().st_size)
                except Exception:
                    pass
                record_pipeline_telemetry(
                    {
                        "run_id": str(uuid4()),
                        "source": "watch_raw",
                        "file_id": inserted_id,
                        "file_name": final_path.name,
                        "file_size": file_size,
                        "file_type": final_path.suffix.lower().lstrip("."),
                        "file_received_time": file_received_time,
                        "preprocessing_start_time": r.preprocessing_start_time,
                        "preprocessing_end_time": r.preprocessing_end_time,
                        "ocr_start_time": r.ocr_start_time,
                        "ocr_end_time": r.ocr_end_time,
                        "gemini_start_time": r.gemini_start_time,
                        "gemini_end_time": r.gemini_end_time,
                        "db_insert_time": db_insert_time,
                        "preprocessing_latency": r.preprocessing_latency,
                        "ocr_latency": r.ocr_latency,
                        "gemini_latency": r.gemini_latency,
                        "gemini_prompt_tokens": r.gemini_prompt_tokens,
                        "gemini_output_tokens": r.gemini_output_tokens,
                        "gemini_total_tokens": r.gemini_total_tokens,
                        "gemini_model": r.gemini_model,
                        "total_pipeline_latency": (db_insert_time - file_received_time).total_seconds(),
                        "status": "success",
                        "error_stage": None,
                        "error_message": None,
                    }
                )
                try:
                    coll = get_invoices_collection()
                    coll.update_one(
                        {"_id": ObjectId(inserted_id)},
                        {
                            "$set": {
                                "gemini.json.additional_fields.HITL": hitl_value,
                                "gemini.json.additional_fields.human_processed": False,
                                "gemini.json.additional_fields.ever_hitl_true": False,
                                "gemini.json.additional_fields.status": status_value,
                            }
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "[Folder watcher → MongoDB] Could not sync HITL/status for %s: %s",
                        inserted_id,
                        e,
                    )
            except Exception as e:
                logger.exception(
                    "[Folder watcher → MongoDB] Failed to store invoice for %s: %s",
                    final_path,
                    e,
                )
                try:
                    from backend.agents.database import record_pipeline_telemetry

                    file_received_time = datetime.utcnow()
                    file_size = 0
                    try:
                        file_size = int(final_path.stat().st_size)
                    except Exception:
                        pass
                    record_pipeline_telemetry(
                        {
                            "run_id": str(uuid4()),
                            "source": "watch_raw",
                            "file_id": None,
                            "file_name": final_path.name,
                            "file_size": file_size,
                            "file_type": final_path.suffix.lower().lstrip("."),
                            "file_received_time": file_received_time,
                            "status": "error",
                            "error_stage": "db",
                            "error_message": str(e),
                            "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
                        }
                    )
                except Exception:
                    pass

            from license_validator import increment_invoice_count

            increment_invoice_count()

            logger.info(
                "[Folder watcher] Pipeline finished. Final location: %s (status=%s, HITL=%s)",
                final_path,
                status_value,
                hitl_value,
            )
            if logger.isEnabledFor(logging.DEBUG) and r.gemini_json is not None:
                logger.debug("Gemini JSON: %s", r.gemini_json)
        except Exception as e:
            logger.exception(
                "[Folder watcher] Pipeline failed for file %s: %s",
                path,
                e,
            )
            if path.is_file():
                try:
                    finalize_invoice_file(
                        path,
                        file_status="error",
                        status=0,
                        pipeline_failed=True,
                    )
                except Exception as move_err:
                    logger.warning(
                        "[Folder watcher] Could not move failed file to ERROR: %s",
                        move_err,
                    )
        finally:
            q.task_done()


def main() -> None:
    configure_logging()
    ensure_invoice_data_layout()

    q: "queue.Queue[Path]" = queue.Queue()
    threading.Thread(target=_worker, args=(q,), daemon=True).start()

    handler = _EnqueueHandler(q)
    observer = Observer()
    observer.schedule(handler, str(TO_BE_PROCESSED_DIR), recursive=False)
    observer.start()

    logger.info(
        "[Folder watcher] Watching to_be_processed: %s | Allowed types: %s | Sequential processing.",
        TO_BE_PROCESSED_DIR,
        sorted(ALLOWED_EXTS),
    )

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
