"""Fuzzy-matches extracted invoice fields (seller, line items, PO number) against
Tally master data stored in MongoDB and mutates the invoice's gemini_json in place.

Scores are 0-100 via rapidfuzz (same scale requested for the feature). A score
at/above ERP_MATCH_THRESHOLD (default 80, see backend/erp_db.get_match_threshold)
is a confirmed match: vendor_id / item_id / PO business_unit get written onto the
invoice. Anything below that — or a field that doesn't match any candidate at all —
is left unmatched and produces a HITL reason string explaining why, so it surfaces
in the same "Reason for Human approval" column the rest of HITL already uses.

This module is a no-op (returns []) whenever ERP master data is not configured
(see backend/erp_db.is_configured), so installs that haven't refreshed Tally
masters yet are unaffected.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from backend import erp_db

logger = logging.getLogger(__name__)


def _vendor_master_label() -> str:
    return "Tally vendors"


def _item_master_label() -> str:
    return "Tally stock items"


def _po_header_label() -> str:
    return "Tally purchase orders"


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _fuzz_ratio(a: str, b: str) -> float:
    from rapidfuzz import fuzz, utils  # local import: keep rapidfuzz optional at module load time

    # default_process lowercases, strips punctuation, and collapses whitespace before
    # comparing — without it, token_sort_ratio is case-sensitive and e.g. "SECURITY
    # GUARD" vs "Security Guard" scores ~15 instead of 100 despite being identical.
    return float(fuzz.token_sort_ratio(a, b, processor=utils.default_process))


def _best_match(
    query: Any,
    candidates: list[dict[str, Any]],
    *,
    key_fields: tuple[str, ...],
) -> tuple[Optional[dict[str, Any]], float]:
    """Best-scoring candidate row across `key_fields` (e.g. try both item_name and
    description, keep whichever field scores higher for each candidate row)."""
    query_norm = _clean(query)
    if not query_norm or not candidates:
        return None, 0.0

    best_row: Optional[dict[str, Any]] = None
    best_score = 0.0
    for row in candidates:
        row_score = 0.0
        for field in key_fields:
            candidate_text = _clean(row.get(field))
            if not candidate_text:
                continue
            score = _fuzz_ratio(query_norm, candidate_text)
            if score > row_score:
                row_score = score
        if row_score > best_score:
            best_score = row_score
            best_row = row

    return best_row, round(best_score, 1)


def match_vendor(seller_name: Any, gst_number: Any = None) -> dict[str, Any]:
    """Fuzzy-match invoice seller against vendor_master.vendor_name. An exact
    GST-number match (when both sides have one) short-circuits to a 100 score
    since it's a much stronger signal than name similarity."""
    threshold = erp_db.get_match_threshold()
    vendors = erp_db.fetch_vendor_master()
    gst_norm = _clean(gst_number)

    if gst_norm:
        for row in vendors:
            if _clean(row.get("gst_tax_number")).upper() == gst_norm.upper():
                return {
                    "matched": True,
                    "vendor_id": row.get("vendor_id"),
                    "vendor_name": row.get("vendor_name"),
                    "score": 100.0,
                }

    best_row, score = _best_match(seller_name, vendors, key_fields=("vendor_name",))
    matched = best_row is not None and score >= threshold
    return {
        "matched": matched,
        "vendor_id": best_row.get("vendor_id") if matched else None,
        "vendor_name": best_row.get("vendor_name") if matched else None,
        "score": score,
        "best_candidate_name": best_row.get("vendor_name") if best_row else None,
    }


def match_item(description: Any) -> dict[str, Any]:
    """Fuzzy-match a line item's service/description against item_master."""
    threshold = erp_db.get_match_threshold()
    items = erp_db.fetch_item_master()
    best_row, score = _best_match(description, items, key_fields=("item_name", "description"))
    matched = best_row is not None and score >= threshold
    return {
        "matched": matched,
        "item_id": best_row.get("item_id") if matched else None,
        "item_name": best_row.get("item_name") if matched else None,
        # Canonical unit-of-measure and category from item_master, for downstream ERP export
        # (see erp_unit / erp_item_group in _match_items_into) - only meaningful when matched.
        "units": best_row.get("units") if matched else None,
        "category": best_row.get("category") if matched else None,
        "score": score,
        "best_candidate_name": best_row.get("item_name") if best_row else None,
    }


def match_po(po_id: Any) -> dict[str, Any]:
    """Match the invoice PO number against po_header.po_id. Exact (case-insensitive)
    match wins when present; otherwise falls back to fuzzy matching to absorb OCR
    noise (e.g. 'PO1O01' vs 'PO1001')."""
    threshold = erp_db.get_match_threshold()
    headers = erp_db.fetch_po_header()
    po_norm = _clean(po_id)
    if not po_norm or not headers:
        return {"matched": False, "po_id": None, "business_unit": None, "vendor_id": None, "score": 0.0}

    for row in headers:
        if _clean(row.get("po_id")).upper() == po_norm.upper():
            return {
                "matched": True,
                "po_id": row.get("po_id"),
                "business_unit": row.get("business_unit"),
                "vendor_id": row.get("vendor_id"),
                "po_status": row.get("po_status"),
                "score": 100.0,
            }

    best_row, score = _best_match(po_norm, headers, key_fields=("po_id",))
    matched = best_row is not None and score >= threshold
    return {
        "matched": matched,
        "po_id": best_row.get("po_id") if matched else None,
        "business_unit": best_row.get("business_unit") if matched else None,
        "vendor_id": best_row.get("vendor_id") if matched else None,
        "po_status": best_row.get("po_status") if matched else None,
        "score": score,
        "best_candidate_po_id": best_row.get("po_id") if best_row else None,
    }


def po_details_for(po_id: str, business_unit: str) -> list[dict[str, Any]]:
    details = erp_db.fetch_po_details()
    po_norm = _clean(po_id).upper()
    bu_norm = _clean(business_unit).upper()
    return [
        row
        for row in details
        if _clean(row.get("po_id")).upper() == po_norm and _clean(row.get("business_unit")).upper() == bu_norm
    ]


def run_erp_matching(gemini_json: dict[str, Any]) -> list[str]:
    """Mutates gemini_json in place with vendor_id / item_id / PO-match results.
    Returns a list of concise HITL reason strings for anything that failed to
    match — the caller (backend/hitl_status.py) folds these into the existing
    hitl_remarks list. Returns [] immediately if ERP master data is not configured."""
    if not erp_db.is_configured():
        return []
    if not isinstance(gemini_json, dict):
        return []

    additional_fields = gemini_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    reasons: list[str] = []
    try:
        reasons.extend(_match_vendor_into(gemini_json, additional_fields))
        reasons.extend(_match_items_into(gemini_json))
        reasons.extend(_match_po_into(gemini_json, additional_fields))
    except Exception as e:
        logger.warning("[ERP matching] Unexpected failure, skipping for this invoice: %s", e)
        reasons.append(f"{erp_db.erp_source_label()} matching failed unexpectedly - verify vendor/item/PO manually")

    return reasons


def _match_vendor_into(gemini_json: dict[str, Any], additional_fields: dict[str, Any]) -> list[str]:
    seller = gemini_json.get("seller")
    if not _clean(seller):
        # Seller is missing entirely - clear any stale match data and flag it explicitly.
        # Previously this returned [] here with no reason, so an invoice with no seller at
        # all could still show as "fully ERP-matched" (nothing to disagree with) and unlock
        # download despite vendor matching never actually having run.
        additional_fields.pop("vendor_id", None)
        additional_fields.pop("erp_vendor_name", None)
        additional_fields["vendor_match_score"] = 0.0
        return [f"Seller/vendor name is missing - cannot match against {_vendor_master_label()}"]

    gst_number = (
        additional_fields.get("gst_number")
        or additional_fields.get("gstin")
        or additional_fields.get("seller_gst_number")
        or additional_fields.get("gst_tax_number")
        or gemini_json.get("gst_number")
    )

    result = match_vendor(seller, gst_number)
    additional_fields["vendor_match_score"] = result["score"]

    if result["matched"]:
        additional_fields["vendor_id"] = result["vendor_id"]
        additional_fields["vendor_match_name"] = result["vendor_name"]
        # Canonical vendor_master.vendor_name, kept separate from the OCR-extracted
        # `seller` field so future ERP/Tally export can use the exact master-data
        # spelling without losing what was actually printed on the invoice.
        additional_fields["erp_vendor_name"] = result["vendor_name"]
        return []

    additional_fields["vendor_id"] = None
    additional_fields["vendor_match_name"] = None
    additional_fields["erp_vendor_name"] = None
    best_name = result.get("best_candidate_name")
    hint = f" (closest match: '{best_name}', score {result['score']:.0f})" if best_name else f" (no vendors in {_vendor_master_label()})"
    return [f"Vendor '{seller}' not found in {_vendor_master_label()}{hint}"]


def _match_items_into(gemini_json: dict[str, Any]) -> list[str]:
    line_items = gemini_json.get("line_items")
    if not isinstance(line_items, list) or not line_items:
        return []

    reasons: list[str] = []
    for idx, li in enumerate(line_items):
        if not isinstance(li, dict):
            continue
        description = li.get("service") or li.get("description")
        if not _clean(description):
            # No service/description to match against item_master at all - clear any
            # stale match data and flag it, rather than silently skipping. Previously
            # this line item was skipped entirely with no HITL reason, so an invoice
            # could look "fully ERP-matched" while one of its line items was never
            # actually checked against Tally master data.
            li["item_match_score"] = 0.0
            li["item_id"] = None
            li["erp_item_name"] = None
            li["erp_unit"] = None
            li["erp_item_group"] = None
            reasons.append(f"Item {idx + 1}: no service/description to match against {_item_master_label()}")
            continue

        result = match_item(description)
        li["item_match_score"] = result["score"]

        if result["matched"]:
            li["item_id"] = result["item_id"]
            # Canonical item_master values, kept separate from the OCR-extracted
            # `service`/`unit` so future ERP/Tally export can use master-data spelling
            # (item name, unit of measure, category/item group) without disturbing the
            # originally extracted invoice text or any quantity/rate/amount/HSN/tax field.
            li["erp_item_name"] = result["item_name"]
            li["erp_unit"] = result["units"]
            li["erp_item_group"] = result["category"]
            continue

        li["item_id"] = None
        li["erp_item_name"] = None
        li["erp_unit"] = None
        li["erp_item_group"] = None
        label = f"Item {idx + 1} ('{description}')"
        best_name = result.get("best_candidate_name")
        hint = f" (closest match: '{best_name}', score {result['score']:.0f})" if best_name else f" (no items in {_item_master_label()})"
        reasons.append(f"{label} not found in {_item_master_label()}{hint}")

    return reasons


def _match_po_into(gemini_json: dict[str, Any], additional_fields: dict[str, Any]) -> list[str]:
    from backend.tally_integration.config import is_po_not_applicable

    po_id = gemini_json.get("po_id")
    if not _clean(po_id) or is_po_not_applicable(po_id):
        # Missing/not-applicable PO is handled elsewhere (calculate_hitl_flag / optional PO mode).
        additional_fields.pop("po_business_unit", None)
        return []

    result = match_po(po_id)
    additional_fields["po_match_score"] = result["score"]

    if not result["matched"]:
        additional_fields["po_business_unit"] = None
        best = result.get("best_candidate_po_id")
        hint = f" (closest match: '{best}', score {result['score']:.0f})" if best else f" (no purchase orders in {_po_header_label()})"
        return [f"PO '{po_id}' not found in {_po_header_label()}{hint}"]

    additional_fields["po_business_unit"] = result["business_unit"]
    reasons: list[str] = []

    matched_vendor_id = additional_fields.get("vendor_id")
    po_vendor_id = result.get("vendor_id")
    if matched_vendor_id and po_vendor_id and _clean(matched_vendor_id).upper() != _clean(po_vendor_id).upper():
        reasons.append(
            f"Vendor on invoice ({matched_vendor_id}) does not match the vendor on PO "
            f"'{result['po_id']}' ({po_vendor_id})"
        )

    details = po_details_for(result["po_id"], result["business_unit"])
    po_item_ids = {_clean(row.get("item_id")).upper() for row in details if row.get("item_id")}

    line_items = gemini_json.get("line_items")
    if isinstance(line_items, list) and po_item_ids:
        for idx, li in enumerate(line_items):
            if not isinstance(li, dict):
                continue
            item_id = li.get("item_id")
            if not item_id:
                continue  # already flagged as "not found in item_master" by _match_items_into
            if _clean(item_id).upper() not in po_item_ids:
                description = li.get("service") or li.get("description") or f"line {idx + 1}"
                reasons.append(
                    f"Item {idx + 1} ('{description}', item_id={item_id}) is not listed on PO '{result['po_id']}'"
                )

    return reasons
