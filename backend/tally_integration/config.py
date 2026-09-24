"""Tally bridge client configuration (main IDP app)."""

from __future__ import annotations

import os
from typing import Any

TALLY_BRIDGE_URL = os.environ.get("TALLY_BRIDGE_URL", "http://localhost:8001").strip().rstrip("/")
TALLY_BRIDGE_TIMEOUT = int(os.environ.get("TALLY_BRIDGE_TIMEOUT", "120"))

TALLY_PUSH_STATUS_KEY = "tally_push_status"
ERP_REMARK_KEY = "erp_remark"
TALLY_ERROR_REASON_KEY = "tally_error_reason"
TALLY_PUSHED_AT_KEY = "tally_pushed_at"
TALLY_PUSH_ATTEMPTS_KEY = "tally_push_attempts"

# Used on invoices with no PO when MANDATORY_PURCHASE_ORDER=0.
PO_NOT_APPLICABLE_VALUE = "Not applicable"

_PO_NOT_APPLICABLE_ALIASES = frozenset(
    {
        "not applicable",
        "not-applicable",
        "n/a",
        "na",
        "none",
        "-",
    }
)


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return False


def is_po_not_applicable(value: Any) -> bool:
    """True when the PO field explicitly means there is no purchase order."""
    if _is_blank(value):
        return False
    normalized = str(value).strip().lower().replace(".", "")
    return normalized in _PO_NOT_APPLICABLE_ALIASES


def is_mandatory_purchase_order() -> bool:
    """When True (MANDATORY_PURCHASE_ORDER=1), missing PO blocks HITL clear and Tally push."""
    raw = os.environ.get("MANDATORY_PURCHASE_ORDER")
    if raw is None or not str(raw).strip():
        return True
    return _truthy(raw, default=True)


def is_hitl_payment_term_required() -> bool:
    """When True (HITL_REQUIRE_TERM_TO_PAY=1), missing due_date and term_to_pay triggers HITL."""
    raw = os.environ.get("HITL_REQUIRE_TERM_TO_PAY")
    if raw is None or not str(raw).strip():
        return False
    return _truthy(raw, default=False)


def resolve_po_id(gemini_json: dict[str, Any]) -> str | None:
    """Return canonical po_id, using Not applicable when PO is optional and missing."""
    if not isinstance(gemini_json, dict):
        return None

    po_id = gemini_json.get("po_id")
    if not _is_blank(po_id):
        text = str(po_id).strip()
        gemini_json["po_id"] = text
        return text

    if not is_mandatory_purchase_order():
        gemini_json["po_id"] = PO_NOT_APPLICABLE_VALUE
        return PO_NOT_APPLICABLE_VALUE

    return None


def po_blocks_tally_push(gemini_json: dict[str, Any]) -> tuple[bool, str | None]:
    """Return (blocked, reason) when mandatory PO is required but missing."""
    if not is_mandatory_purchase_order():
        return False, None
    if not isinstance(gemini_json, dict):
        return True, "Invoice data is missing"
    po_id = gemini_json.get("po_id")
    if _is_blank(po_id):
        return True, "PO ID is required (MANDATORY_PURCHASE_ORDER=1)"
    return False, None


def is_tally_configured() -> bool:
    return _truthy(os.environ.get("TALLY_ENABLED"), default=False) and bool(TALLY_BRIDGE_URL)


def is_push_on_erp_sync_enabled() -> bool:
    return _truthy(os.environ.get("TALLY_PUSH_ON_ERP_SYNC"), default=True)
