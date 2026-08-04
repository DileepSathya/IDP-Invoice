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
from backend.app_paths import app_dir, load_app_dotenv  # noqa: E402
from backend.hitl_status import pipeline_lifecycle_status  # noqa: E402
from backend.invoice_files import (  # noqa: E402
    TO_BE_PROCESSED_DIR,
    ensure_invoice_data_layout,
    finalize_invoice_file,
    list_gemini_api_error_files,
    move_invoice_file,
    move_to_gemini_api_error,
)
from backend.pipeline_errors import (  # noqa: E402
    GEMINI_RECOVERY_MAX_CYCLES,
    GEMINI_RECOVERY_SLEEP_SECONDS,
    GEMINI_RETRY_SLEEP_SECONDS,
    NETWORK_MAX_RETRIES,
    NETWORK_RETRY_SLEEP_SECONDS,
    GeminiApiPipelineError,
    NetworkPipelineError,
    read_gemini_api_key_from_env_file,
    wrap_pipeline_error,
)


load_app_dotenv()
logger = logging.getLogger(__name__)

_shutdown_event = threading.Event()
_processing_lock = threading.Lock()
_processing_active = False
_known_gemini_api_key = read_gemini_api_key_from_env_file()


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


def _set_processing_active(active: bool) -> None:
    global _processing_active
    with _processing_lock:
        _processing_active = active


def _is_processing_active() -> bool:
    with _processing_lock:
        return _processing_active


def _reload_env_and_track_gemini_key() -> str:
    global _known_gemini_api_key
    from dotenv import load_dotenv

    file_key = read_gemini_api_key_from_env_file()
    if file_key != _known_gemini_api_key:
        load_dotenv(app_dir() / ".env", override=True)
        logger.info(
            "[Folder watcher] GEMINI_API_KEY changed in .env — Gemini recovery can retry quarantined files.",
        )
        _known_gemini_api_key = file_key
    return file_key


def _process_file_with_network_retries(path: Path, *, run_id: str | None = None):
    last_exc: Exception | None = None
    for attempt in range(1, NETWORK_MAX_RETRIES + 1):
        try:
            from backend.pipeline_status import clear_pipeline_active, set_pipeline_active

            set_pipeline_active("watch_raw", path)
            try:
                return process_file(str(path), run_id=run_id)
            finally:
                clear_pipeline_active()
        except NetworkPipelineError as exc:
            last_exc = exc
            if attempt >= NETWORK_MAX_RETRIES:
                break
            logger.warning(
                "[Folder watcher] Network error on %s (attempt %s/%s). "
                "Sleeping %ss before retry...",
                path.name,
                attempt,
                NETWORK_MAX_RETRIES,
                NETWORK_RETRY_SLEEP_SECONDS,
            )
            time.sleep(NETWORK_RETRY_SLEEP_SECONDS)
        except GeminiApiPipelineError:
            raise
        except Exception as exc:
            wrapped = wrap_pipeline_error(exc)
            if isinstance(wrapped, NetworkPipelineError):
                last_exc = wrapped
                if attempt >= NETWORK_MAX_RETRIES:
                    break
                logger.warning(
                    "[Folder watcher] Network error on %s (attempt %s/%s). "
                    "Sleeping %ss before retry...",
                    path.name,
                    attempt,
                    NETWORK_MAX_RETRIES,
                    NETWORK_RETRY_SLEEP_SECONDS,
                )
                time.sleep(NETWORK_RETRY_SLEEP_SECONDS)
                continue
            if isinstance(wrapped, GeminiApiPipelineError):
                raise wrapped from exc
            raise

    logger.error(
        "[Folder watcher] Network error persisted after %s attempt(s) for %s. Shutting down watcher.",
        NETWORK_MAX_RETRIES,
        path.name,
    )
    _shutdown_event.set()
    if last_exc is not None:
        raise last_exc
    raise NetworkPipelineError(f"Network failure while processing {path.name}")


def _store_invoice_result(
    path: Path,
    r,
    gemini_json: dict,
    file_status: str,
    *,
    run_id: str | None = None,
) -> None:
    from backend.agents.database import (
        get_invoices_collection,
        link_gemini_metrics_run,
        record_pipeline_telemetry,
        store_invoice_result,
    )

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
        return

    file_received_time = datetime.utcnow()
    try:
        file_received_time = datetime.utcfromtimestamp(path.stat().st_ctime)
    except Exception:
        pass

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
    link_gemini_metrics_run(run_id=run_id, file_id=inserted_id)
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
            "run_id": run_id or str(uuid4()),
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
            "gemini_thoughts_tokens": r.gemini_thoughts_tokens,
            "gemini_cached_content_tokens": r.gemini_cached_content_tokens,
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


def _process_invoice_path(path: Path) -> None:
    configure_logging()
    if not path.is_file():
        logger.info(
            "[Folder watcher → worker] Skipping %s — file no longer present "
            "(likely moved or removed by another process).",
            path,
        )
        return

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
        return

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
        return

    logger.info(
        "[Folder watcher → worker] Starting OCR + Gemini pipeline: %s",
        path,
    )
    run_id = str(uuid4())
    r = _process_file_with_network_retries(path, run_id=run_id)

    gemini_json = r.gemini_json or {}
    if not isinstance(gemini_json, dict):
        gemini_json = {}

    invoice_number = gemini_json.get("invoice_number") or gemini_json.get("invoice")
    invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
    file_status = "error" if not invoice_number_norm else "healthy file"

    if not path.is_file():
        logger.info(
            "[Folder watcher → worker] Skipping finalize for %s — file already moved.",
            path,
        )
        return

    try:
        _store_invoice_result(path, r, gemini_json, file_status, run_id=run_id)
    except Exception as e:
        logger.exception(
            "[Folder watcher → MongoDB] Failed to store invoice for %s: %s",
            path,
            e,
        )
        try:
            from backend.agents.database import record_pipeline_telemetry

            file_received_time = datetime.utcnow()
            file_size = 0
            try:
                file_size = int(path.stat().st_size)
            except Exception:
                pass
            record_pipeline_telemetry(
                {
                    "run_id": str(uuid4()),
                    "source": "watch_raw",
                    "file_id": None,
                    "file_name": path.name,
                    "file_size": file_size,
                    "file_type": path.suffix.lower().lstrip("."),
                    "file_received_time": file_received_time,
                    "status": "error",
                    "error_stage": "db",
                    "error_message": str(e),
                    "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
                }
            )
        except Exception:
            pass


def _record_pipeline_error_telemetry(path: Path, exc: Exception, *, error_stage: str) -> None:
    """Best-effort telemetry record so the dashboard can track cumulative pipeline errors
    (e.g. Gemini quota-exceeded vs. network failures)."""
    try:
        from backend.agents.database import record_pipeline_telemetry

        file_size = 0
        try:
            file_size = int(path.stat().st_size)
        except Exception:
            pass
        now = datetime.utcnow()
        record_pipeline_telemetry(
            {
                "run_id": str(uuid4()),
                "source": "watch_raw",
                "file_id": None,
                "file_name": path.name,
                "file_size": file_size,
                "file_type": path.suffix.lower().lstrip("."),
                "file_received_time": now,
                "status": "error",
                "error_stage": error_stage,
                "error_message": str(exc),
                "total_pipeline_latency": 0.0,
            }
        )
    except Exception:
        logger.debug(
            "[Folder watcher] Could not record telemetry for %s error on %s.",
            error_stage,
            path,
        )


def _handle_gemini_api_failure(path: Path, exc: Exception) -> None:
    logger.error(
        "[Folder watcher] Gemini API error for %s: %s — moving to gemini_api_error folder.",
        path,
        exc,
    )
    _record_pipeline_error_telemetry(path, exc, error_stage="gemini_quota")
    if path.is_file():
        try:
            move_to_gemini_api_error(path)
        except Exception as move_err:
            logger.warning(
                "[Folder watcher] Could not move Gemini-failed file to gemini_api_error: %s",
                move_err,
            )
    logger.info(
        "[Folder watcher] Sleeping %ss after Gemini API error before continuing queue.",
        GEMINI_RETRY_SLEEP_SECONDS,
    )
    time.sleep(GEMINI_RETRY_SLEEP_SECONDS)


def _handle_network_failure(path: Path, exc: Exception) -> None:
    logger.error(
        "[Folder watcher] Network error for %s: %s.",
        path,
        exc,
    )
    _record_pipeline_error_telemetry(path, exc, error_stage="network")


def _handle_generic_failure(path: Path, exc: Exception) -> None:
    logger.exception(
        "[Folder watcher] Pipeline failed for file %s: %s",
        path,
        exc,
    )
    _record_pipeline_error_telemetry(path, exc, error_stage="pipeline")
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


def _requeue_gemini_api_error_files(q: "queue.Queue[Path]") -> int:
    pending = list_gemini_api_error_files()
    if not pending:
        return 0

    requeued = 0
    for source in pending:
        try:
            dest = move_invoice_file(source, TO_BE_PROCESSED_DIR)
            q.put(dest)
            requeued += 1
            logger.info(
                "[Folder watcher → Gemini recovery] Re-queued %s from gemini_api_error.",
                dest.name,
            )
        except Exception as exc:
            logger.warning(
                "[Folder watcher → Gemini recovery] Could not re-queue %s: %s",
                source,
                exc,
            )
    return requeued


def _gemini_recovery_worker(q: "queue.Queue[Path]") -> None:
    previous_key = read_gemini_api_key_from_env_file()
    while not _shutdown_event.is_set():
        if _is_processing_active() or not q.empty():
            time.sleep(1.0)
            continue

        pending = list_gemini_api_error_files()
        if not pending:
            time.sleep(2.0)
            continue

        for cycle in range(1, GEMINI_RECOVERY_MAX_CYCLES + 1):
            if _shutdown_event.is_set():
                return

            logger.info(
                "[Folder watcher → Gemini recovery] Cycle %s/%s — sleeping %ss, "
                "then checking .env for GEMINI_API_KEY changes.",
                cycle,
                GEMINI_RECOVERY_MAX_CYCLES,
                GEMINI_RECOVERY_SLEEP_SECONDS,
            )
            if _shutdown_event.wait(GEMINI_RECOVERY_SLEEP_SECONDS):
                return

            current_key = _reload_env_and_track_gemini_key()
            if current_key == previous_key:
                logger.info(
                    "[Folder watcher → Gemini recovery] GEMINI_API_KEY unchanged — "
                    "skipping retry this cycle.",
                )
                if not list_gemini_api_error_files():
                    break
                continue

            previous_key = current_key
            requeued = _requeue_gemini_api_error_files(q)
            if requeued <= 0:
                break

            while not _shutdown_event.is_set():
                if _is_processing_active() or not q.empty():
                    time.sleep(1.0)
                    continue
                if list_gemini_api_error_files():
                    break
                time.sleep(1.0)

            if not list_gemini_api_error_files():
                logger.info(
                    "[Folder watcher → Gemini recovery] All Gemini-quarantined invoices processed.",
                )
                break

        remaining = list_gemini_api_error_files()
        if remaining:
            logger.warning(
                "[Folder watcher → Gemini recovery] Stopping after %s cycle(s). "
                "%s file(s) remain in gemini_api_error.",
                GEMINI_RECOVERY_MAX_CYCLES,
                len(remaining),
            )
        time.sleep(5.0)


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
    while not _shutdown_event.is_set():
        try:
            path = q.get(timeout=1.0)
        except queue.Empty:
            continue

        _set_processing_active(True)
        try:
            _process_invoice_path(path)
        except NetworkPipelineError as exc:
            _handle_network_failure(path, exc)
            _shutdown_event.set()
            return
        except GeminiApiPipelineError as exc:
            _handle_gemini_api_failure(path, exc)
        except Exception as exc:
            wrapped = wrap_pipeline_error(exc)
            if isinstance(wrapped, NetworkPipelineError):
                _handle_network_failure(path, wrapped)
                _shutdown_event.set()
                return
            if isinstance(wrapped, GeminiApiPipelineError):
                _handle_gemini_api_failure(path, wrapped)
            else:
                _handle_generic_failure(path, exc)
        finally:
            _set_processing_active(False)
            q.task_done()


def main() -> None:
    configure_logging()
    ensure_invoice_data_layout()

    q: "queue.Queue[Path]" = queue.Queue()
    threading.Thread(target=_worker, args=(q,), daemon=True).start()
    threading.Thread(target=_gemini_recovery_worker, args=(q,), daemon=True).start()

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
        while not _shutdown_event.is_set():
            time.sleep(1.0)
    except KeyboardInterrupt:
        observer.stop()
    else:
        if _shutdown_event.is_set():
            logger.error(
                "[Folder watcher] Shutting down due to repeated network errors.",
            )
            observer.stop()
    observer.join()


if __name__ == "__main__":
    main()
