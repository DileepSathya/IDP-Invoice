"""Batch Tally push — runs after ERP sync or on demand."""

from __future__ import annotations

import logging
import threading
from typing import Any, Optional

from bson import ObjectId

from backend.agents.database import get_invoices_collection
from backend.erp_match_status import invoice_erp_matching_complete
from backend.erp_settings import get_erp_sync_settings
from backend.tally_integration import config
from backend.tally_integration.pipeline import persist_tally_result, push_invoice_to_tally

logger = logging.getLogger(__name__)

_tally_thread: threading.Thread | None = None
_tally_lock = threading.Lock()


def _invoice_eligible(gemini_json: dict[str, Any], erp_sync_settings: dict[str, Any]) -> bool:
    if not config.is_tally_configured():
        return False
    blocked, _ = config.po_blocks_tally_push(gemini_json)
    if blocked:
        return False
    if not invoice_erp_matching_complete(gemini_json, erp_sync_settings):
        return False
    additional = gemini_json.get("additional_fields") or {}
    if not isinstance(additional, dict):
        return False
    return additional.get(config.TALLY_PUSH_STATUS_KEY) != "success"


def push_single_invoice_by_id(invoice_id: str, *, force: bool = False) -> dict[str, Any]:
    """Load one invoice and push to Tally. Returns serializable result dict."""
    from backend.erp_match_status import invoice_erp_matching_complete

    coll = get_invoices_collection()
    try:
        oid = ObjectId(invoice_id)
    except Exception as exc:
        raise ValueError("Invalid invoice id") from exc

    doc = coll.find_one({"_id": oid})
    if not doc:
        raise LookupError("Invoice not found")

    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        gemini_json = {}

    blocked, block_reason = config.po_blocks_tally_push(gemini_json)
    if blocked:
        raise PermissionError(block_reason or "PO ID is required")

    erp_settings = get_erp_sync_settings()
    if not force and not invoice_erp_matching_complete(gemini_json, erp_settings):
        raise PermissionError("ERP matching is not complete for this invoice")

    result = push_invoice_to_tally(doc, force=force)
    if not result.skipped:
        persist_tally_result(coll, oid, result)

    return _result_to_dict(result)


def run_tally_sync(*, force: bool = False) -> dict[str, Any]:
    """Push all eligible invoices to Tally."""
    if not config.is_tally_configured():
        logger.info("[tally_sync] Tally not configured – skipping.")
        return {"scanned": 0, "pushed": 0, "skipped": 0, "errored": 0, "skipped_reason": "Tally not configured"}

    if not config.is_push_on_erp_sync_enabled() and not force:
        logger.info("[tally_sync] TALLY_PUSH_ON_ERP_SYNC disabled – skipping.")
        return {"scanned": 0, "pushed": 0, "skipped": 0, "errored": 0, "skipped_reason": "Auto push disabled"}

    coll = get_invoices_collection()
    erp_settings = get_erp_sync_settings()
    scanned = 0
    pushed = 0
    skipped = 0
    errored = 0

    cursor = coll.find({}, no_cursor_timeout=True)
    try:
        for doc in cursor:
            scanned += 1
            gemini_json = (doc.get("gemini") or {}).get("json") or {}
            if not isinstance(gemini_json, dict):
                skipped += 1
                continue

            if not _invoice_eligible(gemini_json, erp_settings):
                skipped += 1
                continue

            try:
                result = push_invoice_to_tally(doc, force=False)
                if result.skipped:
                    skipped += 1
                elif result.success:
                    pushed += 1
                    persist_tally_result(coll, doc["_id"], result)
                else:
                    errored += 1
                    persist_tally_result(coll, doc["_id"], result)
            except Exception as exc:
                errored += 1
                logger.warning("[tally_sync] Failed invoice _id=%s: %s", doc.get("_id"), exc)
    finally:
        cursor.close()

    stats = {"scanned": scanned, "pushed": pushed, "skipped": skipped, "errored": errored}
    _mark_tally_sync_finished(stats)
    logger.info(
        "[tally_sync] Complete: scanned=%d pushed=%d skipped=%d errored=%d",
        scanned,
        pushed,
        skipped,
        errored,
    )
    return stats


def run_tally_sync_async(*, force: bool = False) -> bool:
    """Fire-and-forget batch Tally sync."""
    global _tally_thread

    if not config.is_tally_configured():
        return False

    def _run() -> None:
        try:
            run_tally_sync(force=force)
        except Exception:
            logger.exception("[tally_sync] Background sync failed")

    with _tally_lock:
        if _tally_thread is not None and _tally_thread.is_alive():
            return False
        _tally_thread = threading.Thread(target=_run, name="tally-sync", daemon=True)
        _tally_thread.start()
    return True


def push_invoice_tally_async(invoice_id: str, *, force: bool = False) -> None:
    """Push a single invoice on a background thread (immediate mode)."""

    def _run() -> None:
        try:
            push_single_invoice_by_id(invoice_id, force=force)
        except Exception:
            logger.exception("[tally_sync] Async push failed for invoice_id=%s", invoice_id)

    thread = threading.Thread(
        target=_run, name=f"tally-push-{invoice_id[:8]}", daemon=True
    )
    thread.start()


def _mark_tally_sync_finished(stats: dict[str, Any]) -> None:
    from datetime import datetime

    from backend.agents.database import get_db

    try:
        get_db()["erp_settings"].update_one(
            {"_id": "erp_sync"},
            {
                "$set": {
                    "last_tally_synced_at": datetime.utcnow(),
                    "last_tally_sync_result": stats,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[tally_sync] Could not save sync stats: %s", exc)


def _result_to_dict(result: Any) -> dict[str, Any]:
    return {
        "success": result.success,
        "invoice_id": result.invoice_id,
        "invoice_number": result.invoice_number,
        "erp_remark": result.erp_remark,
        "error_reason": result.error_reason,
        "tally_company": result.tally_company,
        "pushed_at": result.pushed_at.isoformat(),
        "skipped": result.skipped,
    }
