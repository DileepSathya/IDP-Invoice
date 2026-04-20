from __future__ import annotations

import logging
import os
from dataclasses import asdict, is_dataclass
from typing import Any, Optional
from pymongo.errors import CollectionInvalid, OperationFailure
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


def _ensure_timeseries_collection(
    *,
    db: Database,
    coll_name: str,
    time_field: str,
    meta_field: Optional[str] = None,
    granularity: str = "seconds",
) -> Collection:
    existing_names = set(db.list_collection_names())
    if coll_name not in existing_names:
        opts: dict[str, Any] = {
            "timeField": time_field,
            "granularity": granularity,
        }
        if meta_field:
            opts["metaField"] = meta_field
        try:
            db.create_collection(coll_name, timeseries=opts)
        except (CollectionInvalid, OperationFailure):
            # If another process created it concurrently, continue safely.
            pass
    return db[coll_name]


def get_application_event_telemetry_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_TELEMETRY_APP_EVENTS_COLLECTION", "telemetry_app_events")
    coll = get_db()[coll_name]
    try:
        coll.create_index("ts")
        coll.create_index([("event_type", 1), ("ts", -1)])
        coll.create_index([("invoice_id", 1), ("ts", -1)])
    except OperationFailure as e:
        # Keep telemetry inserts working even if existing index options differ.
        logger.warning("[MongoDB] telemetry_app_events index ensure skipped: %s", e)
    return coll


def get_operational_metrics_telemetry_collection() -> Collection:
    load_dotenv()
    db = get_db()
    coll_name = os.environ.get("MONGO_TELEMETRY_OPS_METRICS_COLLECTION", "telemetry_ops_metrics")
    coll = _ensure_timeseries_collection(
        db=db,
        coll_name=coll_name,
        time_field="ts",
        meta_field="meta",
        granularity="seconds",
    )
    coll.create_index([("metric_name", 1), ("ts", -1)])
    coll.create_index([("meta.component", 1), ("ts", -1)])
    return coll


def get_business_snapshot_telemetry_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_TELEMETRY_BUSINESS_SNAPSHOT_COLLECTION", "telemetry_business_snapshots")
    coll = get_db()[coll_name]
    coll.create_index([("snapshot_date", -1), ("period", 1)], unique=True)
    coll.create_index([("generated_at", -1)])
    return coll


def get_hybrid_telemetry_collection() -> Collection:
    load_dotenv()
    coll_name = os.environ.get("MONGO_TELEMETRY_HYBRID_COLLECTION", "telemetry_hybrid")
    coll = get_db()[coll_name]
    coll.create_index([("ts", -1)])
    coll.create_index([("category", 1), ("ts", -1)])
    coll.create_index([("invoice_id", 1), ("ts", -1)])
    return coll


def ensure_telemetry_collections() -> None:
    """
    Ensure all telemetry collections ("tables") exist with indexes:
      1) Application Event Telemetry
      2) Operational Metrics Telemetry (time-series)
      3) Business Snapshot Telemetry
      4) Hybrid Telemetry
    """
    get_application_event_telemetry_collection()
    get_operational_metrics_telemetry_collection()
    get_business_snapshot_telemetry_collection()
    get_hybrid_telemetry_collection()
    logger.info("[MongoDB] Telemetry collections ensured (app_events, ops_metrics, business_snapshots, hybrid).")


def log_application_event(
    *,
    event_type: str,
    payload: Optional[dict[str, Any]] = None,
) -> Optional[str]:
    """
    Insert one application telemetry event document.
    Returns inserted_id as string (or None on best-effort failure).
    """
    try:
        from datetime import datetime

        coll = get_application_event_telemetry_collection()
        doc: dict[str, Any] = {
            "ts": datetime.utcnow(),
            "event_type": str(event_type).strip() or "unknown_event",
        }
        if isinstance(payload, dict):
            doc.update(payload)
        r = coll.insert_one(doc)
        return str(r.inserted_id)
    except Exception as e:
        logger.warning("[MongoDB] Failed to write application telemetry event %r: %s", event_type, e)
        return None


def collect_and_persist_business_telemetry_overview(*, source: str = "system") -> Optional[dict[str, Any]]:
    """
    Compute business telemetry overview from invoices and persist into:
      - telemetry_business_snapshots (daily upsert)
      - telemetry_hybrid (event-like append)
    """
    try:
        from datetime import datetime

        invoices = get_invoices_collection()

        def _to_bool(value: Any) -> bool:
            if isinstance(value, bool):
                return value
            if value is None:
                return False
            if isinstance(value, (int, float)):
                return bool(value)
            text = str(value).strip().lower()
            return text in {"1", "true", "yes", "y", "hit", "hitl"}

        total_uploaded_files = 0
        healthy_files = 0
        error_files = 0
        hitl_flagged_files = 0
        hitl_process_pending = 0
        hitl_processed = 0
        system_processed = 0
        human_approved_files = 0

        for doc in invoices.find({}):
            total_uploaded_files += 1
            file_status = str(doc.get("file_status") or "").strip().lower()
            if file_status == "healthy file":
                healthy_files += 1
            elif file_status == "error":
                error_files += 1

            gemini_json = (doc.get("gemini") or {}).get("json") or {}
            if not isinstance(gemini_json, dict):
                gemini_json = {}
            additional_fields = gemini_json.get("additional_fields") or {}
            if not isinstance(additional_fields, dict):
                additional_fields = {}

            hitl = _to_bool(additional_fields.get("HITL", additional_fields.get("HIT")))
            ever_hitl_true = _to_bool(additional_fields.get("ever_hitl_true"))
            if hitl:
                hitl_flagged_files += 1
                hitl_process_pending += 1
            elif ever_hitl_true:
                hitl_processed += 1
            else:
                system_processed += 1

            if _to_bool(additional_fields.get("human_approved")):
                human_approved_files += 1

        now = datetime.utcnow()
        snapshot_date = now.strftime("%Y-%m-%d")
        overview: dict[str, Any] = {
            "generated_at": now.isoformat() + "Z",
            "total_uploaded_files": total_uploaded_files,
            "healthy_files": healthy_files,
            "error_files": error_files,
            "hitl_flagged_files": hitl_flagged_files,
            "hitl_process_pending": hitl_process_pending,
            "hitl_processed": hitl_processed,
            "system_processed": system_processed,
            "human_approved_files": human_approved_files,
        }

        business_coll = get_business_snapshot_telemetry_collection()
        business_coll.update_one(
            {"snapshot_date": snapshot_date, "period": "daily"},
            {"$set": {**overview, "generated_at": now, "snapshot_date": snapshot_date, "period": "daily"}},
            upsert=True,
        )

        hybrid_coll = get_hybrid_telemetry_collection()
        hybrid_coll.insert_one(
            {
                "ts": now,
                "category": "business_overview",
                "source": source,
                "metrics": overview,
            }
        )
        return overview
    except Exception as e:
        logger.warning("[MongoDB] Failed to persist business telemetry overview: %s", e)
        return None


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

