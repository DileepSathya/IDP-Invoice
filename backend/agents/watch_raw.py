from __future__ import annotations

import logging
import os
import queue
import shutil
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from bson import ObjectId
from dotenv import load_dotenv
from watchdog.events import FileSystemEventHandler
from watchdog.observers import Observer

# Allow running as: `python backend/agents/watch_raw.py` from repo root
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from backend.agents.ocr import ALLOWED_EXTS, process_file  # noqa: E402
from backend.app_logging import configure_logging  # noqa: E402


load_dotenv()
logger = logging.getLogger(__name__)
RAW_DIR = Path(os.environ.get("RAW_DIR", "./invoices_data/raw")).expanduser().resolve()
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "./invoices_data/uploads")).expanduser().resolve()
ERROR_FILES_DIR = Path(os.environ.get("ERROR_FILES_DIR", "./invoices_data/error_files")).expanduser().resolve()
ERROR_FILES_DIR.mkdir(parents=True, exist_ok=True)


def _to_float(value):
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "hit", "hitl"}


def _sum_line_item_amounts_after_tax(line_items):
    if not isinstance(line_items, list) or len(line_items) == 0:
        return None
    running = 0.0
    seen = False
    for li in line_items:
        if not isinstance(li, dict):
            continue
        n = _to_float(li.get("amount_after_tax"))
        if n is None:
            continue
        running += n
        seen = True
    return running if seen else None


def _calculate_summary_total_amount(gemini_json: dict) -> float | None:
    additional_fields = gemini_json.get("additional_fields") or {}
    line_items = gemini_json.get("line_items") or []
    amount_after_tax_sum = _sum_line_item_amounts_after_tax(line_items)
    if amount_after_tax_sum is not None:
        return amount_after_tax_sum
    return _to_float(gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount"))


def _calculate_hitl_status(gemini_json: dict) -> tuple[bool, bool, int]:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    human_approved = _to_bool(additional_fields.get("human_approved"))
    deblurred_applied = _to_bool(additional_fields.get("deblurred_applied"))
    main_total = _to_float(gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount"))
    summary_total = _to_float(additional_fields.get("summary_total_amount"))
    if summary_total is None:
        summary_total = _calculate_summary_total_amount(gemini_json)
        if summary_total is not None:
            additional_fields["summary_total_amount"] = f"{summary_total:.2f}"

    mismatch = False
    if main_total is None and summary_total is None:
        mismatch = False
    elif main_total is None or summary_total is None:
        mismatch = True
    else:
        mismatch = abs(main_total - summary_total) > 0.01

    hitl = False if human_approved else (mismatch or deblurred_applied)
    ever_hitl_true = _to_bool(additional_fields.get("ever_hitl_true")) or hitl
    status = 1 if hitl else (2 if ever_hitl_true else 0)
    return hitl, ever_hitl_true, status


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
                "[Raw folder watcher] A new file appeared under the watched raw folder; "
                "it will be processed after the file finishes copying: %s",
                p,
            )
            self.q.put(p)

    def on_moved(self, event):
        if event.is_directory:
            return
        p = Path(event.dest_path)
        if _is_allowed(p):
            logger.info(
                "[Raw folder watcher] A file was moved into the watched raw folder; "
                "it will be processed after the file is ready: %s",
                p,
            )
            self.q.put(p)


def _worker(q: "queue.Queue[Path]") -> None:
    while True:
        path = q.get()
        try:
            configure_logging()
            logger.info(
                "[Raw folder watcher → worker] Picked file from queue; waiting until the file "
                "is fully written and stable before OCR: %s",
                path,
            )
            _wait_for_complete_write(path)
            logger.info(
                "[Raw folder watcher → worker] File is stable. Starting OCR and data extraction "
                "(same pipeline as HTTP /upload): %s",
                path,
            )
            r = process_file(str(path))

            UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            new_name = f"{path.stem}_{ts}{path.suffix.lower()}"
            uploaded_path = (UPLOADS_DIR / new_name).resolve()
            logger.info(
                "[Raw folder watcher → uploads] Saving a dated copy of the original file under "
                "the uploads folder: %s → %s",
                path,
                uploaded_path,
            )
            shutil.copy2(str(path), str(uploaded_path))

            try:
                from backend.agents.database import (
                    get_invoices_collection,
                    record_pipeline_telemetry,
                    store_invoice_result,
                )

                gemini_json = r.gemini_json or {}
                invoice_number = (
                    gemini_json.get("invoice_number") or gemini_json.get("invoice")
                    if isinstance(gemini_json, dict)
                    else None
                )
                invoice_number_norm = str(invoice_number).strip() if invoice_number is not None else ""
                file_status = "error" if not invoice_number_norm else "healthy file"

                if file_status == "error":
                    ERROR_FILES_DIR.mkdir(parents=True, exist_ok=True)
                    error_path = ERROR_FILES_DIR / uploaded_path.name
                    logger.warning(
                        "[Raw folder watcher → error_files] Invoice number missing; copying upload "
                        "to error_files for review: %s",
                        error_path,
                    )
                    shutil.copy2(str(uploaded_path), str(error_path))

                logger.info(
                    "[Raw folder watcher → MongoDB] Storing OCR text and Gemini extraction in the "
                    "database (collection configured by MONGO_INVOICES_COLLECTION).",
                )
                inserted_id = store_invoice_result(
                    file_path=r.file_path,
                    uploaded_file_path=str(uploaded_path),
                    ocr_text=r.ocr_text,
                    gemini_model=r.gemini_model or (os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"),
                    gemini_json=r.gemini_json,
                    gemini_raw_text=r.gemini_raw_text,
                    file_status=file_status,
                )
                logger.info(
                    "[Raw folder watcher → MongoDB] Insert completed. Document id: %s",
                    inserted_id,
                )
                db_insert_time = datetime.utcnow()
                file_received_time = datetime.utcnow()
                try:
                    file_received_time = datetime.utcfromtimestamp(path.stat().st_ctime)
                except Exception:
                    pass
                file_size = 0
                try:
                    file_size = int(path.stat().st_size)
                except Exception:
                    pass
                record_pipeline_telemetry(
                    {
                        "run_id": str(uuid4()),
                        "source": "watch_raw",
                        "file_id": inserted_id,
                        "file_name": path.name,
                        "file_size": file_size,
                        "file_type": path.suffix.lower().lstrip("."),
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
                # Sync HITL lifecycle fields immediately so telemetry snapshots are accurate.
                try:
                    coll = get_invoices_collection()
                    hitl, ever_hitl_true, status = _calculate_hitl_status(gemini_json if isinstance(gemini_json, dict) else {})
                    coll.update_one(
                        {"_id": ObjectId(inserted_id)},
                        {
                            "$set": {
                                "gemini.json.additional_fields.HITL": hitl,
                                "gemini.json.additional_fields.ever_hitl_true": ever_hitl_true,
                                "gemini.json.additional_fields.status": status,
                            }
                        },
                    )
                except Exception as e:
                    logger.warning(
                        "[Raw folder watcher → MongoDB] Could not sync HITL/status immediately for %s: %s",
                        inserted_id,
                        e,
                    )
            except Exception as e:
                logger.exception(
                    "[Raw folder watcher → MongoDB] Failed to store invoice for %s: %s",
                    path,
                    e,
                )
                try:
                    from backend.agents.database import record_pipeline_telemetry

                    file_received_time = datetime.utcnow()
                    try:
                        file_received_time = datetime.utcfromtimestamp(path.stat().st_ctime)
                    except Exception:
                        pass
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
                            "preprocessing_start_time": None,
                            "preprocessing_end_time": None,
                            "ocr_start_time": None,
                            "ocr_end_time": None,
                            "gemini_start_time": None,
                            "gemini_end_time": None,
                            "db_insert_time": None,
                            "preprocessing_latency": None,
                            "ocr_latency": None,
                            "gemini_latency": None,
                            "gemini_prompt_tokens": None,
                            "gemini_output_tokens": None,
                            "gemini_total_tokens": None,
                            "gemini_model": os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash",
                            "total_pipeline_latency": (datetime.utcnow() - file_received_time).total_seconds(),
                            "status": "error",
                            "error_stage": "db",
                            "error_message": str(e),
                        }
                    )
                except Exception:
                    pass

            logger.info(
                "[Raw folder watcher] Pipeline finished successfully. Source file: %s | "
                "Upload copy: %s",
                r.file_path,
                uploaded_path,
            )
            if logger.isEnabledFor(logging.DEBUG):
                if r.gemini_json is not None:
                    logger.debug("Gemini JSON: %s", r.gemini_json)
                else:
                    logger.debug("Gemini raw text (no JSON): %s", r.gemini_raw_text)
        except Exception as e:
            logger.exception(
                "[Raw folder watcher] Pipeline failed for file %s: %s",
                path,
                e,
            )
        finally:
            q.task_done()


def main() -> None:
    configure_logging()
    RAW_DIR.mkdir(parents=True, exist_ok=True)

    q: "queue.Queue[Path]" = queue.Queue()
    threading.Thread(target=_worker, args=(q,), daemon=True).start()

    handler = _EnqueueHandler(q)
    observer = Observer()
    observer.schedule(handler, str(RAW_DIR), recursive=False)
    observer.start()

    logger.info(
        "[Raw folder watcher] Service started. Watching directory: %s | "
        "Allowed file types: %s | Processing is sequential (one file at a time).",
        RAW_DIR,
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

