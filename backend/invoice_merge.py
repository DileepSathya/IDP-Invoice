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
from copy import deepcopy
from datetime import datetime
from typing import Any

from bson import ObjectId

from backend.hitl_status import ensure_summary_total_amount

logger = logging.getLogger(__name__)

ACTIVE_INVOICE_QUERY: dict[str, Any] = {"merged_into": {"$exists": False}}

# These values describe processing state, not invoice content. Carrying them from
# either page would make the materialized invoice look matched or approved before
# the normal post-merge sync has evaluated the combined data.
_RECOMPUTED_ADDITIONAL_FIELDS = frozenset(
    {
        "HITL",
        "status",
        "hitl_remark",
        "hitl_remarks",
        "human_approved",
        "summary_total_amount",
        "erp_hitl_reasons",
        "vendor_id",
        "vendor_match_score",
        "vendor_match_name",
        "erp_vendor_name",
        "po_match_score",
        "po_business_unit",
        "tally_push_status",
        "tally_error_reason",
    }
)

_RECOMPUTED_LINE_ITEM_FIELDS = frozenset(
    {
        "item_id",
        "item_match_score",
        "ledger_id",
        "ledger_match_score",
        "erp_ledger_name",
        "matched_name",
        "match_score",
        "matching_status",
        "tally_master_id",
    }
)

_LINE_ITEM_IDENTITY_FIELDS = (
    "service",
    "description",
    "hsn_number",
    "HSN_number",
    "HSN",
    "quantity",
    "qty",
    "unit",
    "uom",
    "unit_of_measure",
    "price_per_unit",
    "rate",
    "unit_price",
    "amount",
    "total",
    "tax_rate",
    "tax_amount",
    "amount_after_tax",
)


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


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _values_equivalent(path: str, left: Any, right: Any) -> bool:
    if left == right:
        return True
    leaf = path.rsplit(".", 1)[-1]
    if leaf in {"invoice_number", "invoice"}:
        return normalize_invoice_number(left) == normalize_invoice_number(right)
    return False


def _deep_union(
    canonical: Any,
    incoming: Any,
    *,
    path: str,
    conflicts: list[dict[str, Any]],
) -> Any:
    """Materialize incoming data into canonical data without losing either shape."""
    if _is_blank(canonical):
        return deepcopy(incoming)
    if _is_blank(incoming):
        return deepcopy(canonical)

    if isinstance(canonical, dict) and isinstance(incoming, dict):
        merged = deepcopy(canonical)
        for key, incoming_value in incoming.items():
            child_path = f"{path}.{key}" if path else key
            if key not in merged:
                merged[key] = deepcopy(incoming_value)
            else:
                merged[key] = _deep_union(
                    merged[key], incoming_value, path=child_path, conflicts=conflicts
                )
        return merged

    if isinstance(canonical, list) and isinstance(incoming, list):
        merged = deepcopy(canonical)
        for value in incoming:
            if value not in merged:
                merged.append(deepcopy(value))
        return merged

    if not _values_equivalent(path, canonical, incoming):
        conflict = {
            "path": path,
            "canonical": deepcopy(canonical),
            "incoming": deepcopy(incoming),
        }
        if conflict not in conflicts:
            conflicts.append(conflict)
    return deepcopy(canonical)


def _clean_line_item(item: dict[str, Any]) -> dict[str, Any]:
    return {key: deepcopy(value) for key, value in item.items() if key not in _RECOMPUTED_LINE_ITEM_FIELDS}


def _line_item_identity(item: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    identity: list[tuple[str, str]] = []
    for key in _LINE_ITEM_IDENTITY_FIELDS:
        value = item.get(key)
        if not _is_blank(value):
            identity.append((key, str(value).strip().casefold()))
    return tuple(identity)


def _union_line_items(
    canonical_items: Any,
    incoming_items: Any,
    conflicts: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = [
        _clean_line_item(item)
        for item in (canonical_items if isinstance(canonical_items, list) else [])
        if isinstance(item, dict)
    ]
    identities = {_line_item_identity(item): index for index, item in enumerate(merged)}

    for raw_item in incoming_items if isinstance(incoming_items, list) else []:
        if not isinstance(raw_item, dict):
            continue
        item = _clean_line_item(raw_item)
        identity = _line_item_identity(item)
        if identity and identity in identities:
            index = identities[identity]
            merged[index] = _deep_union(
                merged[index],
                item,
                path=f"line_items[{index}]",
                conflicts=conflicts,
            )
        elif item not in merged:
            identities[identity] = len(merged)
            merged.append(item)
    return merged


def _materialize_merged_json(
    canonical_json: dict[str, Any], incoming_json: dict[str, Any]
) -> dict[str, Any]:
    canonical = deepcopy(canonical_json)
    incoming = deepcopy(incoming_json)

    canonical_additional = canonical.pop("additional_fields", {})
    incoming_additional = incoming.pop("additional_fields", {})
    if not isinstance(canonical_additional, dict):
        canonical_additional = {}
    if not isinstance(incoming_additional, dict):
        incoming_additional = {}

    existing_conflicts: list[dict[str, Any]] = []
    for source in (canonical_additional, incoming_additional):
        raw_conflicts = source.pop("merge_conflicts", [])
        if isinstance(raw_conflicts, list):
            for conflict in raw_conflicts:
                if isinstance(conflict, dict) and conflict not in existing_conflicts:
                    existing_conflicts.append(deepcopy(conflict))
        for key in _RECOMPUTED_ADDITIONAL_FIELDS:
            source.pop(key, None)

    canonical_items = canonical.pop("line_items", [])
    incoming_items = incoming.pop("line_items", [])
    conflicts = existing_conflicts
    merged = _deep_union(canonical, incoming, path="", conflicts=conflicts)
    merged["line_items"] = _union_line_items(canonical_items, incoming_items, conflicts)
    merged_additional = _deep_union(
        canonical_additional,
        incoming_additional,
        path="additional_fields",
        conflicts=conflicts,
    )
    if conflicts:
        merged_additional["merge_conflicts"] = conflicts
    merged["additional_fields"] = merged_additional
    return merged


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

    canonical_json = _materialize_merged_json(canonical_json, incoming_json)

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
                "merge_materialized_at": datetime.utcnow(),
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
        # Backfill records produced by the older reference-only merge. The deep
        # union is idempotent, and the marker prevents repeated work on later scans.
        if not merged_doc.get("merge_materialized_at"):
            try:
                canonical_id = ObjectId(str(merged_doc.get("merged_into")))
            except Exception:
                canonical_id = None
            canonical = coll.find_one({"_id": canonical_id}) if canonical_id else None
            if canonical:
                _merge_document_into_canonical(coll, canonical, merged_doc)
                refreshed = coll.find_one({"_id": merged_doc["_id"]})
                if refreshed:
                    merged_doc = refreshed
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
