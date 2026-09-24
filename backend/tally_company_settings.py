"""Read and persist Tally company and connection settings from the Settings UI.

Settings live in MongoDB (erp_settings collection) so the API, Tally bridge,
and background jobs always agree on the active company and Tally URL without
editing .env.
"""

from __future__ import annotations

import logging
import os
import re
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from backend.agents.database import get_db
from backend.app_paths import tally_bridge_root

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "tally_company"
_COMPANY_ENV_KEY = "TALLY_COMPANY"
_URL_ENV_KEY = "TALLY_URL"
_DEFAULT_TALLY_PORT = 9000
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


def _normalize_tally_host(tally_host: str) -> str:
    normalized = (tally_host or "").strip()
    if not normalized:
        raise ValueError("IP address is required.")
    if "\n" in normalized or "\r" in normalized or " " in normalized:
        raise ValueError("IP address must be a single token.")
    return normalized


def _normalize_tally_port(tally_port: int | str) -> int:
    try:
        value = int(tally_port)
    except (TypeError, ValueError) as exc:
        raise ValueError("Port must be a whole number.") from exc
    if value < 1 or value > 65535:
        raise ValueError("Port must be between 1 and 65535.")
    return value


def _parse_tally_url(url: str) -> tuple[str, int]:
    raw = (url or "").strip()
    if not raw:
        return "", _DEFAULT_TALLY_PORT
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip()
    port = parsed.port if parsed.port is not None else _DEFAULT_TALLY_PORT
    return host, port


def _build_tally_url(host: str, port: int) -> str:
    return f"http://{host}:{port}"


def _read_env_value(key: str) -> str:
    path = _env_path()
    if not path.is_file():
        return ""
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _ENV_LINE_RE.match(line)
        if match and match.group(1) == key:
            value = match.group(2).strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            return value.strip()
    return ""


def _legacy_company_from_env() -> str:
    return _read_env_value(_COMPANY_ENV_KEY) or os.environ.get(_COMPANY_ENV_KEY, "").strip()


def _legacy_tally_url_from_env() -> str:
    return _read_env_value(_URL_ENV_KEY) or os.environ.get(_URL_ENV_KEY, "").strip()


def _migrate_from_env_if_needed() -> None:
    """One-time import of TALLY_COMPANY / TALLY_URL from bridge .env when MongoDB is empty."""
    global _migration_done
    if _migration_done:
        return
    _migration_done = True

    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
        updates: dict[str, object] = {}

        if not (doc.get("company_name") or "").strip():
            legacy_company = _legacy_company_from_env()
            if legacy_company:
                try:
                    updates["company_name"] = _normalize_company_name(legacy_company)
                except ValueError:
                    logger.warning(
                        "[tally_company_settings] Skipping invalid legacy TALLY_COMPANY during migration."
                    )

        if not (doc.get("tally_host") or "").strip():
            legacy_url = _legacy_tally_url_from_env()
            if legacy_url:
                host, port = _parse_tally_url(legacy_url)
                if host:
                    updates["tally_host"] = host
                    updates["tally_port"] = port

        if not updates:
            return

        updates["updated_at"] = datetime.utcnow()
        updates["migrated_from_env"] = True
        _collection().update_one(
            {"_id": _SETTINGS_DOC_ID},
            {"$set": updates},
            upsert=True,
        )
        logger.info("[tally_company_settings] Migrated Tally settings from .env to MongoDB.")
    except Exception as exc:
        logger.warning("[tally_company_settings] Could not migrate from .env: %s", exc)


def _host_port_from_doc(doc: dict) -> tuple[str, int]:
    host = (doc.get("tally_host") or "").strip()
    port_raw = doc.get("tally_port")
    if port_raw is None:
        port = _DEFAULT_TALLY_PORT
    else:
        try:
            port = int(port_raw)
        except (TypeError, ValueError):
            port = _DEFAULT_TALLY_PORT
    return host, port


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


def current_tally_url() -> str:
    """Return the Tally HTTP URL from MongoDB (with one-time .env migration)."""
    _migrate_from_env_if_needed()
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
        host, port = _host_port_from_doc(doc)
        if host:
            return _build_tally_url(host, port)
    except Exception as exc:
        logger.warning("[tally_company_settings] Could not read Tally URL from MongoDB: %s", exc)

    legacy = _legacy_tally_url_from_env()
    if legacy:
        return legacy
    return _build_tally_url("localhost", _DEFAULT_TALLY_PORT)


def tallY_user_credential():
    user_name = os.environ.get("TALLY_USER_NAME", "").strip()
    user_password = os.environ.get("TALLY_USER_PASSWORD", "").strip()
    return user_name, user_password


def get_tally_company() -> dict[str, str | int]:
    _migrate_from_env_if_needed()
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as exc:
        logger.warning("[tally_company_settings] Could not read settings: %s", exc)
        doc = {}

    host, port = _host_port_from_doc(doc)
    if not host:
        legacy = _legacy_tally_url_from_env()
        if legacy:
            host, port = _parse_tally_url(legacy)

    return {
        "company_name": current_tally_company(),
        "tally_host": host,
        "tally_port": port,
    }


def save_tally_company(
    company_name: str,
    tally_host: str,
    tally_port: int,
) -> dict[str, str | int]:
    normalized_name = _normalize_company_name(company_name)
    normalized_host = _normalize_tally_host(tally_host)
    normalized_port = _normalize_tally_port(tally_port)
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "company_name": normalized_name,
                "tally_host": normalized_host,
                "tally_port": normalized_port,
                "updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info("[tally_company_settings] Saved Tally company and connection settings to MongoDB.")
    return {
        "company_name": normalized_name,
        "tally_host": normalized_host,
        "tally_port": normalized_port,
    }
