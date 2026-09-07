"""Full-invoice ERP re-sync against Tally master data in MongoDB.

Re-runs matching for every stored invoice so changes after a Tally master refresh
(or invoice edits in scheduled mode) propagate to HITL status and download gates.

Triggered manually (ERP Force Sync) or via backend/erp_scheduler.py.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend import erp_db, erp_settings
from backend.agents.database import get_invoices_collection
from backend.erp_match_status import ERP_MATCH_PENDING_KEY
from backend.hitl_status import calculate_hitl_flag, calculate_status_from_hitl, to_bool
from backend.invoice_files import relocate_after_hitl_processed
from backend.invoice_merge import ACTIVE_INVOICE_QUERY, merge_hitl_duplicate_invoices

logger = logging.getLogger(__name__)


def _sync_one_invoice(coll: Any, doc: dict[str, Any]) -> bool:
    """Re-runs HITL/ERP evaluation for one stored invoice and persists any
    change. Returns True if anything actually changed (used for the
    scanned/updated counts shown on the ERP page)."""
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        return False

    additional_fields = gemini_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    before = {
        "HITL": additional_fields.get("HITL"),
        "hitl_remark": additional_fields.get("hitl_remark"),
        "vendor_id": additional_fields.get("vendor_id"),
        "erp_vendor_name": additional_fields.get("erp_vendor_name"),
        "po_business_unit": additional_fields.get("po_business_unit"),
        "line_items": [
            (
                li.get("match_type") or li.get("line_match_type"),
                li.get("matched_name"),
                li.get("match_score"),
                li.get("matching_status"),
                li.get("tally_master_id"),
                li.get("original_name"),
                li.get("item_id"),
                li.get("item_match_score"),
                li.get("erp_item_name"),
                li.get("ledger_id"),
                li.get("ledger_match_score"),
                li.get("erp_ledger_name"),
            )
            for li in (gemini_json.get("line_items") or [])
            if isinstance(li, dict)
        ],
    }

    human_processed = to_bool(
        additional_fields.get("human_processed") or additional_fields.get("ever_hitl_true")
    )

    hitl_value = calculate_hitl_flag(gemini_json)
    additional_fields = gemini_json.get("additional_fields") or {}
    additional_fields[ERP_MATCH_PENDING_KEY] = False
    status_value = calculate_status_from_hitl(hitl_value=hitl_value, human_processed=human_processed)

    update_doc: dict[str, Any] = {
        "gemini.json.additional_fields.HITL": hitl_value,
        "gemini.json.additional_fields.status": status_value,
        "gemini.json.additional_fields.hitl_remark": additional_fields.get("hitl_remark", ""),
        "gemini.json.additional_fields.hitl_remarks": additional_fields.get("hitl_remarks", []),
        f"gemini.json.additional_fields.{ERP_MATCH_PENDING_KEY}": False,
    }
    for key in (
        "vendor_id",
        "vendor_match_name",
        "vendor_match_score",
        "erp_vendor_name",
        "po_match_score",
        "po_business_unit",
        "erp_hitl_reasons",
    ):
        if key in additional_fields:
            update_doc[f"gemini.json.additional_fields.{key}"] = additional_fields.get(key)
    if isinstance(gemini_json.get("line_items"), list):
        update_doc["gemini.json.line_items"] = gemini_json.get("line_items")

    after_line_items = [
        (
            li.get("match_type") or li.get("line_match_type"),
            li.get("matched_name"),
            li.get("match_score"),
            li.get("matching_status"),
            li.get("tally_master_id"),
            li.get("original_name"),
            li.get("item_id"),
            li.get("item_match_score"),
            li.get("erp_item_name"),
            li.get("ledger_id"),
            li.get("ledger_match_score"),
            li.get("erp_ledger_name"),
        )
        for li in (gemini_json.get("line_items") or [])
        if isinstance(li, dict)
    ]
    changed = (
        before["HITL"] != hitl_value
        or before["hitl_remark"] != additional_fields.get("hitl_remark")
        or before["vendor_id"] != additional_fields.get("vendor_id")
        or before["erp_vendor_name"] != additional_fields.get("erp_vendor_name")
        or before["po_business_unit"] != additional_fields.get("po_business_unit")
        or before["line_items"] != after_line_items
    )

    # Keep the physical file in sync with the Mongo status it now resolves to. Without
    # this, a doc whose ERP mismatch just got fixed by this sync (HITL True -> False,
    # no human involved) would report status=0 ("system processed") while its file is
    # still sitting in HITL_pending - the "Processed" count on the Dashboard would look
    # like it moved without the file actually being there, and it'd disagree with the
    # folder-based HITL pending count.
    if to_bool(before["HITL"]) and not hitl_value:
        uploaded_file_path = doc.get("uploaded_file_path")
        try:
            new_path = relocate_after_hitl_processed(uploaded_file_path)
        except Exception as e:
            new_path = None
            logger.warning(
                "[erp_sync] Could not relocate file for resolved invoice _id=%s: %s",
                doc.get("_id"),
                e,
            )
        if new_path and new_path != uploaded_file_path:
            update_doc["uploaded_file_path"] = new_path
            update_doc["file_path"] = new_path

    coll.update_one({"_id": doc["_id"]}, {"$set": update_doc})
    return changed


def run_erp_sync() -> dict[str, Any]:
    """Synchronous full re-sync. Safe to call directly (blocks until done) or
    via run_erp_sync_async() from a request handler. No-ops (without taking
    the sync lock) if ERP master data isn't configured, so this is harmless to call
    speculatively."""
    if not erp_db.is_configured():
        logger.info("[erp_sync] ERP master data not configured - skipping sync.")
        return {"scanned": 0, "updated": 0, "errored": 0, "skipped_reason": "ERP master data not configured"}

    if not erp_settings.mark_sync_started():
        logger.info("[erp_sync] Sync already in progress - skipping this trigger.")
        return {"scanned": 0, "updated": 0, "errored": 0, "skipped_reason": "Sync already in progress"}

    # Fresh Tally master cache for this pass.
    erp_db.invalidate_cache()

    coll = get_invoices_collection()
    scanned = 0
    updated = 0
    errored = 0
    merge_result = {"groups_found": 0, "docs_merged": 0}
    try:
        merge_result = merge_hitl_duplicate_invoices(coll)
        if merge_result.get("docs_merged"):
            logger.info(
                "[erp_sync] Merged duplicate invoice numbers: groups=%d docs=%d",
                merge_result.get("groups_found", 0),
                merge_result.get("docs_merged", 0),
            )

        cursor = coll.find(ACTIVE_INVOICE_QUERY, no_cursor_timeout=True)
        try:
            for doc in cursor:
                scanned += 1
                try:
                    if _sync_one_invoice(coll, doc):
                        updated += 1
                except Exception as e:
                    errored += 1
                    logger.warning(
                        "[erp_sync] Failed to re-sync invoice _id=%s: %s", doc.get("_id"), e
                    )
        finally:
            cursor.close()
    finally:
        erp_settings.mark_sync_finished(
            scanned=scanned,
            updated=updated,
            errored=errored,
            merged_groups=merge_result.get("groups_found", 0),
            merged_docs=merge_result.get("docs_merged", 0),
        )

    logger.info(
        "[erp_sync] Sync complete: scanned=%d updated=%d errored=%d merged_groups=%d merged_docs=%d",
        scanned,
        updated,
        errored,
        merge_result.get("groups_found", 0),
        merge_result.get("docs_merged", 0),
    )

    from backend.tally_integration.config import is_push_on_erp_sync_enabled, is_tally_configured
    from backend.tally_sync import run_tally_sync_async

    if is_tally_configured() and is_push_on_erp_sync_enabled():
        run_tally_sync_async()

    return {
        "scanned": scanned,
        "updated": updated,
        "errored": errored,
        "merged_groups": merge_result.get("groups_found", 0),
        "merged_docs": merge_result.get("docs_merged", 0),
    }


def run_erp_sync_async() -> bool:
    """Fire-and-forget: starts run_erp_sync() on a background daemon thread so
    an API request (the Force Sync button) returns immediately instead of
    blocking on a full scan of every stored invoice. Returns False without
    starting anything if a sync is already running."""
    settings = erp_settings.get_erp_sync_settings()
    if settings.get("syncing"):
        return False

    thread = threading.Thread(target=run_erp_sync, name="erp-force-sync", daemon=True)
    thread.start()
    return True
