"""Shared HITL flag and lifecycle status helpers."""

from __future__ import annotations

from typing import Any


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return bool(value)
    text = str(value).strip().lower()
    return text in {"1", "true", "yes", "y", "hit", "hitl"}


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        return None


def _sum_line_item_amounts_after_tax(line_items: list) -> float | None:
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


def ensure_summary_total_amount(gemini_json: dict[str, Any]) -> None:
    additional_fields = gemini_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    if _to_float(additional_fields.get("summary_total_amount")) is not None:
        return

    line_items = gemini_json.get("line_items") or []
    amount_after_tax_sum = _sum_line_item_amounts_after_tax(line_items)
    if amount_after_tax_sum is not None:
        additional_fields["summary_total_amount"] = f"{amount_after_tax_sum:.2f}"
        return

    main_total = _to_float(
        gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount")
    )
    if main_total is not None:
        additional_fields["summary_total_amount"] = f"{main_total:.2f}"


def calculate_hitl_flag(gemini_json: dict[str, Any]) -> bool:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    if to_bool(additional_fields.get("human_approved")):
        return False

    ensure_summary_total_amount(gemini_json)
    additional_fields = gemini_json.get("additional_fields") or {}

    deblurred_applied = to_bool(additional_fields.get("deblurred_applied"))
    main_total = _to_float(
        gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount")
    )
    summary_total_amount = _to_float(additional_fields.get("summary_total_amount"))

    mismatch = False
    if main_total is None and summary_total_amount is None:
        mismatch = False
    elif main_total is None or summary_total_amount is None:
        mismatch = True
    else:
        mismatch = abs(main_total - summary_total_amount) > 0.01

    return mismatch or deblurred_applied


def calculate_status_from_hitl(*, hitl_value: bool, human_processed: bool) -> int:
    # 0: system processed, 1: HITL pending, 2: HITL processed
    if human_processed:
        return 2
    if hitl_value:
        return 1
    return 0


def pipeline_lifecycle_status(gemini_json: dict[str, Any], *, file_status: str) -> tuple[bool, int]:
    """HITL flag and status immediately after OCR/Gemini (before human review)."""
    if file_status == "error":
        return False, 0
    ensure_summary_total_amount(gemini_json)
    hitl_value = calculate_hitl_flag(gemini_json)
    status_value = calculate_status_from_hitl(hitl_value=hitl_value, human_processed=False)
    return hitl_value, status_value
