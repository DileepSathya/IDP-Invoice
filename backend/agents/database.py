from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta
from dataclasses import asdict, is_dataclass
from typing import Any, Optional
from pymongo.errors import OperationFailure
from pymongo.errors import ServerSelectionTimeoutError

from dotenv import load_dotenv


def normalize_gemini_json_for_storage(gemini_json: Optional[Any]) -> Optional[dict[str, Any]]:
    """
    Normalize gemini.json payload before insert.
    We intentionally avoid auto-creating workflow flags like status/payment_status.
    """
    if gemini_json is None:
        return None
    if not isinstance(gemini_json, dict):
        return gemini_json  # type: ignore[return-value]
    return dict(gemini_json)
from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database


logger = logging.getLogger(__name__)

_CLIENT: Optional[MongoClient] = None


def _get_client() -> MongoClient:
    global _CLIENT
    if _CLIENT is None:
        load_dotenv()
        uri = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
        
        logger.info(
            "[MongoDB] Opening connection (first use in this process) — URI host from env "
            "MONGO_URI, default mongodb://localhost:27017."
        )
        
        _CLIENT = MongoClient(uri, serverSelectionTimeoutMS=5000)

        # ✅ Force connection check
        try:
            _CLIENT.admin.command("ping")
            logger.info("[MongoDB] Connection established successfully ✅")
        except ServerSelectionTimeoutError as e:
            logger.error(f"[MongoDB] Connection failed ❌: {e}")
            _CLIENT = None
            raise

    return _CLIENT


def get_db() -> Database:
    load_dotenv()
    db_name = os.environ.get("MONGO_DB", "IDP")
    return _get_client()[db_name]


def get_invoices_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_INVOICES_COLLECTION", "invoices")
    db_name = os.environ.get("MONGO_DB", "IDP")
    coll = get_db()[coll_name]

    # "Ensure" collection by ensuring indexes; collection will exist after first write.
    coll.create_index("file_path")
    logger.debug(
        "[MongoDB] Using database %r, collection %r (indexes ensured on file_path).",
        db_name,
        coll_name,
    )
    return coll


def get_pipeline_runs_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_PIPELINE_RUNS_COLLECTION", "pipeline_runs")
    coll = get_db()[coll_name]
    coll.create_index([("created_at", -1)])
    coll.create_index([("run_id", 1)], unique=True)
    coll.create_index([("status", 1), ("created_at", -1)])
    coll.create_index([("error_stage", 1), ("created_at", -1)])
    coll.create_index([("file_id", 1)])
    return coll


def get_pipeline_metrics_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_PIPELINE_METRICS_COLLECTION", "pipeline_metrics_timeseries")
    coll = get_db()[coll_name]
    coll.create_index([("ts", -1)])
    return coll


def store_invoice_result(
    *,
    file_path: str,
    uploaded_file_path: Optional[str] = None,
    ocr_text: str,
    gemini_model: str,
    gemini_json: Optional[Any],
    gemini_raw_text: str,
    file_status: Optional[str] = None,
) -> str:
    """
    Inserts one document into MongoDB and returns inserted_id as string.
    """
    logger.info(
        "[MongoDB] Inserting invoice document — file_path=%s | uploaded_file_path=%s",
        file_path,
        uploaded_file_path or "(none)",
    )
    coll = get_invoices_collection()
    normalized_json = normalize_gemini_json_for_storage(gemini_json)
    doc: dict[str, Any] = {
        "file_path": file_path,
        "uploaded_file_path": uploaded_file_path,
        "ocr_text": ocr_text,
        "file_status": file_status,
        "gemini": {
            "model": gemini_model,
            "raw_text": gemini_raw_text,
            "json": normalized_json,
        },
    }
    r = coll.insert_one(doc)
    inserted = str(r.inserted_id)
    logger.info(
        "[MongoDB] Insert succeeded — saved to database _id=%s | file_status=%s | "
        "uploaded_file_path=%s",
        inserted,
        file_status,
        uploaded_file_path or "(none)",
    )
    return inserted


def store_ocr_result(result: Any) -> str:
    """
    Convenience wrapper for `backend.agents.ocr.OcrResult`.
    """
    d = asdict(result) if is_dataclass(result) else dict(result)
    gemini_model = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    return store_invoice_result(
        file_path=d["file_path"],
        uploaded_file_path=d.get("uploaded_file_path"),
        ocr_text=d["ocr_text"],
        gemini_model=gemini_model,
        gemini_json=d.get("gemini_json"),
        gemini_raw_text=d["gemini_raw_text"],
    )


def record_pipeline_telemetry(run_doc: dict[str, Any]) -> Optional[str]:
    """
    Persist one pipeline run record and append an aggregate metrics snapshot.
    """
    try:
        runs_coll = get_pipeline_runs_collection()
        metrics_coll = get_pipeline_metrics_collection()
        invoices_coll = get_invoices_collection()

        doc = dict(run_doc)
        now = datetime.utcnow()
        doc.setdefault("created_at", now)
        inserted = runs_coll.insert_one(doc)

        total = runs_coll.count_documents({})
        success = runs_coll.count_documents({"status": "success"})
        failure = runs_coll.count_documents({"status": "error"})
        one_min_ago = now - timedelta(minutes=1)
        processed_per_min = runs_coll.count_documents({"created_at": {"$gte": one_min_ago}})

        latency_cursor = runs_coll.aggregate(
            [
                {"$match": {"total_pipeline_latency": {"$type": "number"}}},
                {"$group": {"_id": None, "avg_latency": {"$avg": "$total_pipeline_latency"}}},
            ]
        )
        latency_row = next(latency_cursor, None)
        avg_pipeline_latency = float(latency_row["avg_latency"]) if latency_row and latency_row.get("avg_latency") is not None else 0.0

        success_rate = (success / total) if total else 0.0
        total_invoices_stored = invoices_coll.count_documents({})

        metrics_coll.insert_one(
            {
                "ts": now,
                "invoices_processed_total": int(total),
                "invoices_processed_per_minute": float(processed_per_min),
                "pipeline_latency_seconds": float(avg_pipeline_latency),
                "pipeline_success_count": int(success),
                "pipeline_failure_count": int(failure),
                "pipeline_success_rate": float(success_rate),
                "total_invoices_stored": int(total_invoices_stored),
            }
        )
        return str(inserted.inserted_id)
    except Exception as e:
        logger.warning("[MongoDB] Failed to persist pipeline telemetry: %s", e)
        return None

