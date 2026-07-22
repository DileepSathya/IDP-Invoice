"""Shared HITL flag and lifecycle status helpers."""

from __future__ import annotations

from typing import Any

# Absolute currency tolerance for the invoice-level total vs. sum-of-line-items check.
_TOTAL_AMOUNT_TOLERANCE = 0.01

_DISCOUNT_KEYS = ("discount", "discount_amount", "total_discount")
_ROUND_OFF_KEYS = (
    "round_off",
    "roundoff",
    "round_off_amount",
    "square_off",
    "square_off_amount",
    "rounding",
)
_SGST_KEYS = ("sgst_amount",)
_CGST_KEYS = ("cgst_amount",)
_IGST_KEYS = ("igst_amount", "total_igst_amount")

# Gemini doesn't always use the same key for the PO number across invoices;
# normalize whichever one it used into a single canonical "po_id" field.
_PO_ID_KEYS = (
    "po_id",
    "PO_ID",
    "po_number",
    "PO_number",
    "PO_Number",
    "purchase_order",
    "Purchase_Order",
    "purchase_order_number",
    "purchase_order_id",
    "purchase_id",
    "Purchase_Id",
    "Purchase_ID",
    "PO",
)

# Same idea for payment terms ("Net 30", "30 days", "60 days", ...) - Gemini
# may label this term_of_payment, payment_term(s), net_days, credit_period, etc.
_TERM_TO_PAY_KEYS = (
    "term_to_pay",
    "Term_to_Pay",
    "term_of_payment",
    "Term_of_Payment",
    "payment_term",
    "payment_terms",
    "Payment_Term",
    "Payment_Terms",
    "net_days",
    "net_day",
    "Net_Days",
    "Net_Day",
    "credit_period",
    "Credit_Period",
    "credit_days",
    "Credit_Days",
)


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


def _first_float(d: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for k in keys:
        v = _to_float(d.get(k))
        if v is not None:
            return v
    return None


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


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


def _sum_line_item_amounts(line_items: list) -> float | None:
    if not isinstance(line_items, list) or len(line_items) == 0:
        return None
    running = 0.0
    seen = False
    for li in line_items:
        if not isinstance(li, dict):
            continue
        n = _to_float(li.get("amount") or li.get("total"))
        if n is None:
            continue
        running += n
        seen = True
    return running if seen else None


def compute_expected_total(gemini_json: dict[str, Any]) -> float | None:
    """
    Best-effort "true" invoice total from line items plus billing-summary adjustments.

    Discount, round-off/square-off, and CGST/SGST/IGST can each show up either baked into
    the line items (via amount_after_tax) or as invoice-level additional_fields (billing
    summary). This tries to use whichever source has the data without double-counting tax
    that is already embedded in each line item's amount_after_tax.
    """
    additional_fields = gemini_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    line_items = gemini_json.get("line_items") or []

    amount_after_tax_sum = _sum_line_item_amounts_after_tax(line_items)
    if amount_after_tax_sum is not None:
        # Line items already carry their own tax; do not add billing-summary tax again.
        base = amount_after_tax_sum
    else:
        base = _sum_line_item_amounts(line_items)
        if base is None:
            base = _first_float(additional_fields, ("sub_total", "subtotal_after_discount"))
        if base is None:
            base = _to_float(gemini_json.get("amount"))
        if base is not None:
            sgst = _first_float(additional_fields, _SGST_KEYS) or 0.0
            cgst = _first_float(additional_fields, _CGST_KEYS) or 0.0
            igst = _first_float(additional_fields, _IGST_KEYS) or 0.0
            base = base + sgst + cgst + igst

    if base is None:
        return None

    discount = _first_float(additional_fields, _DISCOUNT_KEYS) or 0.0
    round_off = _first_float(additional_fields, _ROUND_OFF_KEYS) or 0.0
    return base - discount + round_off


def normalize_po_id(gemini_json: dict[str, Any]) -> str | None:
    """Collapse whichever PO-number-like key Gemini used this time (po_number,
    purchase_order, purchase_id, PO_ID, ...) into a single canonical top-level
    "po_id" field on gemini_json, mutating it in place, and return that value.

    A human can also type "Not Applicable" into the PO ID field when an
    invoice genuinely has no PO — that's treated as present (non-blank), not
    missing, so it clears the HITL flag for good instead of re-flagging forever.
    """
    if not isinstance(gemini_json, dict):
        return None

    existing = gemini_json.get("po_id")
    if not _is_blank(existing):
        gemini_json["po_id"] = str(existing).strip()
        return gemini_json["po_id"]

    for key in _PO_ID_KEYS:
        value = gemini_json.get(key)
        if not _is_blank(value):
            text = str(value).strip()
            gemini_json["po_id"] = text
            return text

    gemini_json["po_id"] = gemini_json.get("po_id")  # keep key present (None) for a stable shape
    return None


def normalize_term_to_pay(gemini_json: dict[str, Any]) -> str | None:
    """Collapse whichever payment-term key Gemini used this time (term_of_payment,
    payment_term(s), net_days, credit_period, ...) into a single canonical
    top-level "term_to_pay" field on gemini_json, mutating it in place, and
    return that value. Values like "Net 30", "30 days", "60 days" all pass
    through as-is (free text), since the exact wording varies by invoice."""
    if not isinstance(gemini_json, dict):
        return None

    existing = gemini_json.get("term_to_pay")
    if not _is_blank(existing):
        gemini_json["term_to_pay"] = str(existing).strip()
        return gemini_json["term_to_pay"]

    for key in _TERM_TO_PAY_KEYS:
        value = gemini_json.get(key)
        if not _is_blank(value):
            text = str(value).strip()
            gemini_json["term_to_pay"] = text
            return text

    gemini_json["term_to_pay"] = gemini_json.get("term_to_pay")  # keep key present (None)
    return None


def ensure_summary_total_amount(gemini_json: dict[str, Any]) -> None:
    additional_fields = gemini_json.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    if _to_float(additional_fields.get("summary_total_amount")) is not None:
        return

    expected_total = compute_expected_total(gemini_json)
    if expected_total is not None:
        additional_fields["summary_total_amount"] = f"{expected_total:.2f}"
        return

    main_total = _to_float(
        gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount")
    )
    if main_total is not None:
        additional_fields["summary_total_amount"] = f"{main_total:.2f}"


def _line_item_label(index: int, li: dict[str, Any]) -> str:
    service = li.get("service") or li.get("description")
    label = f"Item {index + 1}"
    if service:
        short_service = str(service).strip()
        if len(short_service) > 24:
            short_service = short_service[:24].rstrip() + "..."
        label = f"{label} ({short_service})"
    return label


def _amount_math_tolerance(expected: float) -> float:
    # Allow a little slack for OCR/rounding noise, but keep it tight enough to catch real errors.
    return max(0.02, abs(expected) * 0.01)


def evaluate_line_items(line_items: Any) -> tuple[bool, list[str]]:
    """
    Validate each line item:
      - quantity and rate (price_per_unit) must both be present, else flag for HITL.
      - when both are present, quantity * rate must equal the line item amount, else flag for HITL.

    Returns (flagged, concise_reasons) - each reason is a short, single-line point.
    """
    flagged = False
    reasons: list[str] = []

    if not isinstance(line_items, list) or len(line_items) == 0:
        return flagged, reasons

    for idx, li in enumerate(line_items):
        if not isinstance(li, dict):
            continue
        label = _line_item_label(idx, li)

        qty_raw = li.get("quantity", li.get("qty"))
        rate_raw = li.get("price_per_unit", li.get("rate", li.get("unit_price")))

        qty_missing = _is_blank(qty_raw)
        rate_missing = _is_blank(rate_raw)
        if qty_missing or rate_missing:
            missing_parts = []
            if qty_missing:
                missing_parts.append("quantity")
            if rate_missing:
                missing_parts.append("rate")
            reasons.append(f"{label}: missing {' & '.join(missing_parts)}")
            flagged = True
            continue

        qty = _to_float(qty_raw)
        rate = _to_float(rate_raw)
        if qty is None or rate is None:
            reasons.append(f"{label}: quantity/rate not numeric")
            flagged = True
            continue

        amount_raw = li.get("amount", li.get("total"))
        amount = _to_float(amount_raw)
        if amount is None:
            reasons.append(f"{label}: amount missing")
            flagged = True
            continue

        expected = qty * rate
        if abs(expected - amount) > _amount_math_tolerance(expected):
            reasons.append(
                f"{label}: qty x rate != amount ({expected:.2f} vs {amount:.2f})"
            )
            flagged = True

    return flagged, reasons


def calculate_hitl_flag(gemini_json: dict[str, Any], *, run_erp_matching_now: bool = True) -> bool:
    additional_fields = gemini_json.get("additional_fields") or {}
    if not isinstance(additional_fields, dict):
        additional_fields = {}
        gemini_json["additional_fields"] = additional_fields

    ensure_summary_total_amount(gemini_json)
    additional_fields = gemini_json.get("additional_fields") or {}

    human_approved = to_bool(additional_fields.get("human_approved"))

    reasons: list[str] = []

    # A human reviewing/editing the record already looked at the scan, so image quality
    # alone should not keep re-flagging it - but date, totals, and line-item math are
    # objective data checks that must hold even after a human edit.
    deblurred_applied = False
    if not human_approved:
        deblurred_applied = to_bool(additional_fields.get("deblurred_applied"))
        if deblurred_applied:
            reasons.append("Low-quality scan - verify values")

    invoice_date = gemini_json.get("invoice_date") or gemini_json.get("date")
    date_missing = _is_blank(invoice_date)
    if date_missing:
        reasons.append("Invoice date is missing")

    po_id_value = normalize_po_id(gemini_json)
    po_id_missing = _is_blank(po_id_value)
    if po_id_missing:
        reasons.append("PO ID is missing")

    # Due date and term-to-pay ("Net 30", "30 days", ...) are alternatives for
    # the same underlying question - when payment is due. Either one being
    # present is enough; only flag when BOTH are missing.
    due_date_value = gemini_json.get("due_date")
    term_to_pay_value = normalize_term_to_pay(gemini_json)
    payment_term_missing = _is_blank(due_date_value) and _is_blank(term_to_pay_value)
    if payment_term_missing:
        reasons.append("Term to pay / Due date is missing")

    main_total = _to_float(
        gemini_json.get("total_amount") or gemini_json.get("grand_total") or gemini_json.get("amount")
    )
    summary_total_amount = _to_float(additional_fields.get("summary_total_amount"))

    total_mismatch = False
    if main_total is None and summary_total_amount is None:
        total_mismatch = False
    elif main_total is None or summary_total_amount is None:
        total_mismatch = True
        reasons.append("Total vs line items: one value missing")
    else:
        total_mismatch = abs(main_total - summary_total_amount) > _TOTAL_AMOUNT_TOLERANCE
        if total_mismatch:
            reasons.append(
                f"Total {main_total:.2f} vs line items {summary_total_amount:.2f} - mismatch"
            )

    line_items = gemini_json.get("line_items") or []
    line_items_flagged, line_item_reasons = evaluate_line_items(line_items)
    reasons.extend(line_item_reasons)

    # PO_DB (Postgres) vendor / item / PO cross-checks. Optional - a no-op that
    # returns [] whenever PO_DB isn't configured (see backend/erp_db.is_configured),
    # so installs that haven't set up Postgres are unaffected. Wrapped defensively
    # so a PO_DB outage or bad data never breaks the rest of HITL evaluation.
    #
    # `run_erp_matching_now=False` skips hitting Postgres altogether and instead reuses
    # whatever reasons the *last real* match run found (stored on the doc). This matters
    # because calculate_hitl_flag() also runs on hot read paths (the invoice list endpoint,
    # polled every few seconds by the UI; chat/analytics scans over every invoice) - without
    # this, an unreachable/misconfigured Postgres would get hammered on every single poll
    # instead of matching once at ingest/edit time and again only on the next Force Sync or
    # scheduled batch (backend/erp_sync.py).
    erp_reasons: list[str] = []
    if run_erp_matching_now:
        try:
            from backend.erp_matching import run_erp_matching

            erp_reasons = run_erp_matching(gemini_json)
        except Exception:
            erp_reasons = []
        additional_fields["erp_hitl_reasons"] = erp_reasons
    else:
        cached = additional_fields.get("erp_hitl_reasons")
        erp_reasons = list(cached) if isinstance(cached, list) else []
    reasons.extend(erp_reasons)

    hitl_value = bool(
        date_missing
        or total_mismatch
        or deblurred_applied
        or line_items_flagged
        or po_id_missing
        or payment_term_missing
        or erp_reasons
    )

    additional_fields["hitl_remarks"] = reasons
    additional_fields["hitl_remark"] = "; ".join(reasons)

    if hitl_value and human_approved:
        # A human previously approved this record, but re-validation still finds issues -
        # clear the stale approval so the UI does not claim it is resolved.
        additional_fields["human_approved"] = False

    return hitl_value


def calculate_status_from_hitl(*, hitl_value: bool, human_processed: bool) -> int:
    # 0: system processed, 1: HITL pending, 2: HITL processed
    # An outstanding HITL flag always wins, even on records a human already touched -
    # editing does not "clear" a record until the underlying data actually checks out.
    if hitl_value:
        return 1
    if human_processed:
        return 2
    return 0


def pipeline_lifecycle_status(gemini_json: dict[str, Any], *, file_status: str) -> tuple[bool, int]:
    """HITL flag and status immediately after OCR/Gemini (before human review)."""
    if file_status == "error":
        return False, 0
    ensure_summary_total_amount(gemini_json)
    hitl_value = calculate_hitl_flag(gemini_json)
    status_value = calculate_status_from_hitl(hitl_value=hitl_value, human_processed=False)
    return hitl_value, status_value
