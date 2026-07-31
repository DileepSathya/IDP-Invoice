"""Tally integration — bridge client to TALLY INTEGRATION service."""

from backend.tally_integration.bridge_client import TallyPushResult, ping_bridge
from backend.tally_integration.config import (
    ERP_REMARK_KEY,
    TALLY_ERROR_REASON_KEY,
    TALLY_PUSHED_AT_KEY,
    TALLY_PUSH_ATTEMPTS_KEY,
    TALLY_PUSH_STATUS_KEY,
    is_push_on_erp_sync_enabled,
    is_tally_configured,
)
from backend.tally_integration.pipeline import (
    display_erp_remark,
    persist_tally_result,
    push_invoice_to_tally,
)

__all__ = [
    "ERP_REMARK_KEY",
    "TALLY_ERROR_REASON_KEY",
    "TALLY_PUSHED_AT_KEY",
    "TALLY_PUSH_ATTEMPTS_KEY",
    "TALLY_PUSH_STATUS_KEY",
    "TallyPushResult",
    "display_erp_remark",
    "is_push_on_erp_sync_enabled",
    "is_tally_configured",
    "persist_tally_result",
    "ping_bridge",
    "push_invoice_to_tally",
]
