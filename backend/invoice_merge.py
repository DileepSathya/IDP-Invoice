"""Merge duplicate invoice numbers into a single HITL-pending record.

Rules (see product discussion):
- If an older record for the same invoice number is system-processed (status 0) or
  human-reviewed (status 2), later duplicates stay standalone — no merge.
- Otherwise, multiple HITL-pending (status 1) documents with the same invoice
  number are merged into the oldest HITL-pending canonical record.
- Merged-away documents are marked with ``merged_into`` and excluded from lists,
  telemetry, Tally push, and the erp_sync scan (except as merge sources).

Triggered from ``backend/erp_sync.run_erp_sync`` before per-invoice re-matching.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime
from typing import Any

from bson import ObjectId

from backend.hitl_status import ensure_summary_total_amount

logger = logging.getLogger(__name__)

ACTIVE_INVOICE_QUERY: dict[str, Any] = {"merged_into": {"$exists": False}}


def is_merged_away(doc: dict[str, Any]) -> bool:
    return bool(doc.get("merged_into"))


def normalize_invoice_number(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text.casefold()


def invoice_number_from_doc(doc: dict[str, Any]) -> str | None:
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        return None
    return normalize_invoice_number(gemini_json.get("invoice_number") or gemini_json.get("invoice"))


def _doc_status(doc: dict[str, Any]) -> int | None:
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(gemini_json, dict):
        return None
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        return None
    raw = additional_fields.get("status")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def _file_paths(doc: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for key in ("uploaded_file_path", "file_path"):
        value = doc.get(key)
        if value and str(value).strip() and str(value) not in paths:
            paths.append(str(value))
    for value in doc.get("source_files") or []:
        if value and str(value).strip() and str(value) not in paths:
            paths.append(str(value))
    return paths


def preview_file_paths(doc: dict[str, Any]) -> list[str]:
    """All file paths that should be previewable for this invoice (merged pages included)."""
    paths = _file_paths(doc)
    if not paths:
        return []
    return paths


def _merge_document_into_canonical(coll: Any, canonical: dict[str, Any], incoming: dict[str, Any]) -> bool:
    canonical_id = canonical["_id"]
    incoming_id = incoming["_id"]
    if canonical_id == incoming_id:
        return False

    canonical_json = (canonical.get("gemini") or {}).get("json") or {}
    incoming_json = (incoming.get("gemini") or {}).get("json") or {}
    if not isinstance(canonical_json, dict):
        canonical_json = {}
    if not isinstance(incoming_json, dict):
        incoming_json = {}

    canonical_items = canonical_json.get("line_items")
    if not isinstance(canonical_items, list):
        canonical_items = []
    incoming_items = incoming_json.get("line_items")
    if isinstance(incoming_items, list) and incoming_items:
        canonical_items = list(canonical_items) + [li for li in incoming_items if isinstance(li, dict)]
        canonical_json["line_items"] = canonical_items

    canonical_paths = _file_paths(canonical)
    for path in _file_paths(incoming):
        from backend.invoice_files import archive_merged_source_file

        archived = archive_merged_source_file(path) or path
        if archived not in canonical_paths:
            canonical_paths.append(archived)

    canonical_ocr = str(canonical.get("ocr_text") or "").strip()
    incoming_ocr = str(incoming.get("ocr_text") or "").strip()
    if incoming_ocr:
        if canonical_ocr:
            merged_ocr = f"{canonical_ocr}\n\n--- merged document ---\n\n{incoming_ocr}"
        else:
            merged_ocr = incoming_ocr
    else:
        merged_ocr = canonical_ocr

    additional_fields = canonical_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        canonical_json["additional_fields"] = additional_fields
    additional_fields.pop("summary_total_amount", None)

    ensure_summary_total_amount(canonical_json)

    merged_from = list(canonical.get("merged_from") or [])
    incoming_id_str = str(incoming_id)
    if incoming_id_str not in merged_from:
        merged_from.append(incoming_id_str)

    coll.update_one(
        {"_id": canonical_id},
        {
            "$set": {
                "gemini.json": canonical_json,
                "ocr_text": merged_ocr,
                "source_files": canonical_paths,
                "merged_from": merged_from,
            }
        },
    )
    coll.update_one(
        {"_id": incoming_id},
        {
            "$set": {
                "merged_into": str(canonical_id),
                "merged_at": datetime.utcnow(),
                "gemini.json.additional_fields.HITL": False,
                "gemini.json.additional_fields.status": 0,
                "gemini.json.additional_fields.hitl_remark": "",
                "gemini.json.additional_fields.hitl_remarks": [],
            }
        },
    )
    logger.info(
        "[invoice_merge] Merged invoice _id=%s into canonical _id=%s (invoice_number=%s)",
        incoming_id,
        canonical_id,
        invoice_number_from_doc(canonical),
    )
    return True


def _finalize_merged_away_doc(coll: Any, doc: dict[str, Any]) -> None:
    """Ensure merged-away records no longer count as HITL or occupy HITL_pending."""
    from backend.invoice_files import archive_merged_source_file

    doc_id = doc["_id"]
    updates: dict[str, Any] = {}
    gemini_json = (doc.get("gemini") or {}).get("json") or {}
    additional_fields = gemini_json.get("additional_fields") if isinstance(gemini_json, dict) else {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    if additional_fields.get("status") == 1 or additional_fields.get("HITL"):
        updates["gemini.json.additional_fields.HITL"] = False
        updates["gemini.json.additional_fields.status"] = 0
        updates["gemini.json.additional_fields.hitl_remark"] = ""
        updates["gemini.json.additional_fields.hitl_remarks"] = []

    archived_paths: list[str] = []
    for path in _file_paths(doc):
        archived = archive_merged_source_file(path)
        if archived:
            archived_paths.append(archived)

    if archived_paths:
        updates["source_files"] = archived_paths
        updates["uploaded_file_path"] = archived_paths[0]
        updates["file_path"] = archived_paths[0]

    if updates:
        coll.update_one({"_id": doc_id}, {"$set": updates})


def merge_hitl_duplicate_invoices(coll: Any) -> dict[str, int]:
    """Scan active invoices, merge eligible HITL-pending duplicates.

    Returns counts: ``groups_found`` (duplicate groups merged) and ``docs_merged``.
    """
    from backend.erp_settings import should_merge_hitl_duplicates

    if not should_merge_hitl_duplicates():
        return {"groups_found": 0, "docs_merged": 0}

    for merged_doc in coll.find({"merged_into": {"$exists": True, "$ne": None}}):
        _finalize_merged_away_doc(coll, merged_doc)

    docs = list(coll.find(ACTIVE_INVOICE_QUERY))
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for doc in docs:
        key = invoice_number_from_doc(doc)
        if key:
            groups[key].append(doc)

    groups_found = 0
    docs_merged = 0

    for _key, group in groups.items():
        if len(group) < 2:
            continue

        group.sort(key=lambda d: d["_id"])
        hitl_pending = [d for d in group if _doc_status(d) == 1]
        if len(hitl_pending) < 2:
            continue

        canonical = hitl_pending[0]
        canonical_id = canonical["_id"]

        if _has_older_processed_sibling(group, canonical_id):
            continue

        group_had_merge = False
        for doc in group:
            if doc["_id"] == canonical_id:
                continue
            if _doc_status(doc) != 1:
                continue
            if _merge_document_into_canonical(coll, canonical, doc):
                docs_merged += 1
                group_had_merge = True
                refreshed = coll.find_one({"_id": canonical_id})
                if refreshed:
                    canonical = refreshed

        if group_had_merge:
            groups_found += 1

    return {"groups_found": groups_found, "docs_merged": docs_merged}


def merge_and_resync_after_insert(coll: Any, inserted_id: str) -> str:
    """Run merge pass after ingest and re-sync the surviving invoice record.

    Returns the Mongo id that remains active (canonical when a merge occurred).
    """
    merge_hitl_duplicate_invoices(coll)
    doc = coll.find_one({"_id": ObjectId(inserted_id)})
    if not doc:
        return inserted_id

    target_doc = doc
    if doc.get("merged_into"):
        canonical = coll.find_one({"_id": ObjectId(doc["merged_into"])})
        if canonical:
            target_doc = canonical

    from backend.erp_sync import _sync_one_invoice

    _sync_one_invoice(coll, target_doc)
    return str(target_doc["_id"])


def _has_older_processed_sibling(group: list[dict[str, Any]], canonical_id: ObjectId) -> bool:
    for doc in group:
        if doc["_id"] >= canonical_id:
            break
        if _doc_status(doc) in (0, 2):
            return True
    return False
