"""Read and persist the Tally company selected in the Settings UI.

Settings live in MongoDB (erp_settings collection) so the API, Tally bridge,
and background jobs always agree on the active company name without editing .env.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path

from backend.agents.database import get_db
from backend.app_paths import tally_bridge_root

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "tally_company"
_COMPANY_ENV_KEY = "TALLY_COMPANY"
_ENV_LINE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=(.*)$")
_migration_done = False


def _collection():
    return get_db()["erp_settings"]


def _env_path() -> Path:
    return tally_bridge_root() / ".env"


def _normalize_company_name(company_name: str) -> str:
    normalized = (company_name or "").strip()
    if not normalized:
        raise ValueError("Company name is required.")
    if "\n" in normalized or "\r" in normalized:
        raise ValueError("Company name must be a single line.")
    return normalized


def _read_company_from_file() -> str:
    path = _env_path()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == _COMPANY_ENV_KEY:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value.strip()
    return ""


def _legacy_company_from_env() -> str:
    return _read_company_from_file() or os.environ.get(_COMPANY_ENV_KEY, "").strip()


def _migrate_from_env_if_needed() -> None:
    """One-time import of TALLY_COMPANY from bridge .env when MongoDB has no value."""
    global _migration_done
    if _migration_done:
        return
    _migration_done = True

    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
        if (doc.get("company_name") or "").strip():
            return

        legacy = _legacy_company_from_env()
        if not legacy:
            return

        try:
            normalized = _normalize_company_name(legacy)
        except ValueError:
            logger.warning(
                "[tally_company_settings] Skipping invalid legacy TALLY_COMPANY during migration."
            )
            return

        _collection().update_one(
            {"_id": _SETTINGS_DOC_ID},
            {
                "$set": {
                    "company_name": normalized,
                    "updated_at": datetime.utcnow(),
                    "migrated_from_env": True,
                }
            },
            upsert=True,
        )
        logger.info(
            "[tally_company_settings] Migrated TALLY_COMPANY from .env to MongoDB: %s",
            normalized,
        )
    except Exception as exc:
        logger.warning("[tally_company_settings] Could not migrate from .env: %s", exc)


def current_tally_company() -> str:
    """Return the latest saved company name from MongoDB (with one-time .env migration)."""
    _migrate_from_env_if_needed()
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
        name = (doc.get("company_name") or "").strip()
        if name:
            return name
    except Exception as exc:
        logger.warning("[tally_company_settings] Could not read from MongoDB: %s", exc)

    return _legacy_company_from_env()


def tallY_user_credential():
    user_name = os.environ.get("TALLY_USER_NAME", "").strip()
    user_password = os.environ.get("TALLY_USER_PASSWORD", "").strip()
    return user_name, user_password


def get_tally_company() -> dict[str, str]:
    return {"company_name": current_tally_company()}


def save_tally_company(company_name: str) -> dict[str, str]:
    normalized = _normalize_company_name(company_name)
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {"$set": {"company_name": normalized, "updated_at": datetime.utcnow()}},
        upsert=True,
    )
    logger.info("[tally_company_settings] Saved Tally company to MongoDB.")
    return {"company_name": normalized}
