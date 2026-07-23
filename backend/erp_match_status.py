"""Whether an invoice has completed a current PO_DB (ERP) match — used to gate JSON download."""

from __future__ import annotations

from typing import Any, Optional

from backend import erp_db

ERP_MATCH_PENDING_KEY = "erp_match_pending"


def _additional_fields(gemini_json: dict[str, Any]) -> dict[str, Any]:
    additional_fields = gemini_json.get("additional_fields")
    if isinstance(additional_fields, dict):
        return additional_fields
    return {}


def invoice_erp_matching_complete(
    gemini_json: dict[str, Any],
    erp_sync_settings: Optional[dict[str, Any]] = None,
) -> bool:
    """True when PO_DB is configured, no sync is in progress, this invoice is not
    waiting for a deferred scheduled re-match, and the last ERP match run left no
    unmatched vendor/item/PO reasons."""
    if not erp_db.is_configured():
        return False

    if erp_sync_settings is None:
        from backend.erp_settings import get_erp_sync_settings

        erp_sync_settings = get_erp_sync_settings()

    if erp_sync_settings.get("syncing"):
        return False

    additional_fields = _additional_fields(gemini_json)
    if additional_fields.get(ERP_MATCH_PENDING_KEY):
        return False

    erp_reasons = additional_fields.get("erp_hitl_reasons")
    if not isinstance(erp_reasons, list):
        return False

    return len(erp_reasons) == 0
