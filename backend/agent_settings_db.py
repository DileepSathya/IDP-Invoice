"""Persist AI model + Gemini API key in MongoDB."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "ai_agent"


def _collection():
    return get_db()["app_settings"]


def get_stored_agent_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID})
        return doc if isinstance(doc, dict) else {}
    except Exception as exc:
        logger.warning("[agent_settings_db] Could not read AI settings: %s", exc)
        return {}


def get_stored_agent_settings_with_status() -> tuple[dict[str, Any], bool]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID})
        return (doc if isinstance(doc, dict) else {}), True
    except Exception as exc:
        logger.warning("[agent_settings_db] Could not read AI settings: %s", exc)
        return {}, False


def save_stored_agent_settings(*, model: str, gemini_api_key: str) -> None:
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "model": model,
                "gemini_api_key": gemini_api_key,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info("[agent_settings_db] Saved AI agent settings to MongoDB (model=%s).", model)


def get_stored_gemini_api_key() -> Optional[str]:
    doc, db_ok = get_stored_agent_settings_with_status()
    if not db_ok:
        return None
    key = str(doc.get("gemini_api_key") or "").strip()
    return key or None
