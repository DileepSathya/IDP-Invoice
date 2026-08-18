"""Persisted Tally integration settings (purchase ledger selection)."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "tally_settings"
DEFAULT_PURCHASE_LEDGER = (
    os.environ.get("TALLY_PURCHASE_LEDGER", "Purchase A/c").strip() or "Purchase A/c"
)


def _collection():
    return get_db()["erp_settings"]


def get_tally_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as exc:
        logger.warning("[tally_settings] Could not read settings, using defaults: %s", exc)
        doc = {}

    purchase_ledger = str(doc.get("purchase_ledger") or "").strip() or DEFAULT_PURCHASE_LEDGER
    return {
        "purchase_ledger": purchase_ledger,
        "updated_at": doc.get("updated_at"),
    }


def save_tally_settings(*, purchase_ledger: str) -> dict[str, Any]:
    purchase_ledger = (purchase_ledger or "").strip()
    if not purchase_ledger:
        raise ValueError("purchase_ledger is required")

    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "purchase_ledger": purchase_ledger,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info("[tally_settings] Saved purchase ledger: %s", purchase_ledger)
    return get_tally_settings()


def purchase_ledger_updated_at_iso(settings: Optional[dict[str, Any]] = None) -> Optional[str]:
    s = settings or get_tally_settings()
    updated = s.get("updated_at")
    if updated is None:
        return None
    if hasattr(updated, "isoformat"):
        return updated.isoformat()
    return str(updated)
