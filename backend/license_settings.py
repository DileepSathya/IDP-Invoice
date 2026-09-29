"""Persist application license key and quota period state in MongoDB."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "application_license"


def _collection():
    return get_db()["license_settings"]


def get_license_document() -> dict[str, Any]:
    doc, _ = get_license_document_with_status()
    return doc


def get_license_document_with_status() -> tuple[dict[str, Any], bool]:
    """Return (document, mongodb_reachable)."""
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID})
        return (doc if isinstance(doc, dict) else {}), True
    except Exception as exc:
        logger.warning("[license_settings] Could not read license document: %s", exc)
        return {}, False


def get_stored_license_key() -> Optional[str]:
    key, _ = get_stored_license_key_with_status()
    return key


def get_stored_license_key_with_status() -> tuple[Optional[str], bool]:
    doc, db_ok = get_license_document_with_status()
    if not db_ok:
        return None, False
    key = str(doc.get("license_key") or "").strip()
    return (key or None), True


def save_license_key(license_key: str) -> None:
    normalized = "".join(str(license_key or "").split())
    if not normalized:
        raise ValueError("License key is required.")
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "license_key": normalized,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info("[license_settings] License key saved to MongoDB.")


def get_period_state() -> dict[str, Any]:
    doc = get_license_document()
    return {
        "periodKey": doc.get("period_key"),
        "issuedAt": doc.get("period_issued_at"),
        "customerId": doc.get("period_customer_id"),
    }


def save_period_state(*, period_key: str, issued_at: str, customer_id: str) -> None:
    try:
        _collection().update_one(
            {"_id": _SETTINGS_DOC_ID},
            {
                "$set": {
                    "period_key": period_key,
                    "period_issued_at": issued_at,
                    "period_customer_id": customer_id,
                }
            },
            upsert=True,
        )
    except Exception as exc:
        logger.warning("[license_settings] Could not save quota period state: %s", exc)
