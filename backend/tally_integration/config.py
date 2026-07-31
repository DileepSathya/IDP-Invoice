"""Tally bridge client configuration (main IDP app)."""

from __future__ import annotations

import os

TALLY_BRIDGE_URL = os.environ.get("TALLY_BRIDGE_URL", "http://localhost:8001").strip().rstrip("/")
TALLY_BRIDGE_TIMEOUT = int(os.environ.get("TALLY_BRIDGE_TIMEOUT", "120"))

TALLY_PUSH_STATUS_KEY = "tally_push_status"
ERP_REMARK_KEY = "erp_remark"
TALLY_ERROR_REASON_KEY = "tally_error_reason"
TALLY_PUSHED_AT_KEY = "tally_pushed_at"
TALLY_PUSH_ATTEMPTS_KEY = "tally_push_attempts"


def _truthy(value: str | None, *, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in ("true", "1", "yes", "on")


def is_tally_configured() -> bool:
    return _truthy(os.environ.get("TALLY_ENABLED"), default=False) and bool(TALLY_BRIDGE_URL)


def is_push_on_erp_sync_enabled() -> bool:
    return _truthy(os.environ.get("TALLY_PUSH_ON_ERP_SYNC"), default=True)
