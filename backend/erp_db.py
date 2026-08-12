"""Read-only PostgreSQL access to the ERP reference data in PO_DB: vendor_master,
item_master, po_header, po_details. Schema: PO_DB/sql/create_po_database.sql.

This module is intentionally optional. If POSTGRES_HOST is not set in the
environment (see .env.example), `is_configured()` returns False and every fetch_*
function returns an empty list without raising — callers (backend/erp_matching.py)
treat that as "ERP matching not set up yet" and skip matching rather than failing
the invoice pipeline. Once a real Postgres connection fails (wrong password, DB
down, etc.) fetches also degrade to [] (logged as a warning) for the same reason:
a PO_DB outage should never block invoice ingestion.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any, Optional

from backend.app_paths import load_app_dotenv

logger = logging.getLogger(__name__)

# Reference tables change rarely; cache each table's rows for a short time so a
# burst of invoices doesn't re-query Postgres per invoice/line item.
_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def is_configured() -> bool:
    load_app_dotenv()
    return bool(os.environ.get("POSTGRES_HOST") or os.environ.get("POSTGRES_DSN"))


def get_match_threshold() -> float:
    load_app_dotenv()
    try:
        return float(os.environ.get("ERP_MATCH_THRESHOLD", "80"))
    except (TypeError, ValueError):
        return 80.0


def _connection_host(host: str) -> str:
    normalized = (host or "").strip().lower()
    if normalized in {"localhost", ""}:
        return "127.0.0.1"
    return host.strip()


def _get_conn():
    load_app_dotenv()
    import psycopg2  # local import: keep psycopg2 optional until ERP is configured/used

    dsn = os.environ.get("POSTGRES_DSN")
    if dsn:
        return psycopg2.connect(dsn, connect_timeout=int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5")))
    return psycopg2.connect(
        host=_connection_host(os.environ.get("POSTGRES_HOST", "localhost")),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "PO_DB"),
        user=os.environ.get("POSTGRES_USER", "postgres"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
        connect_timeout=int(os.environ.get("POSTGRES_CONNECT_TIMEOUT", "5")),
    )


def _query(cache_key: str, sql: str) -> list[dict[str, Any]]:
    if not is_configured():
        return []

    now = time.monotonic()
    cached = _cache.get(cache_key)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        conn = _get_conn()
    except Exception as e:
        logger.warning("[PO_DB] Could not connect to Postgres for %s: %s", cache_key, e)
        return cached[1] if cached else []

    try:
        import psycopg2.extras

        with conn:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(sql)
                rows = [dict(r) for r in cur.fetchall()]
        _cache[cache_key] = (now, rows)
        return rows
    except Exception as e:
        logger.warning("[PO_DB] Query failed for %s: %s", cache_key, e)
        return cached[1] if cached else []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def fetch_vendor_master() -> list[dict[str, Any]]:
    return _query("vendor_master", "SELECT * FROM vendor_master")


def fetch_item_master() -> list[dict[str, Any]]:
    return _query("item_master", "SELECT * FROM item_master")


def fetch_po_header() -> list[dict[str, Any]]:
    return _query("po_header", "SELECT * FROM po_header")


def fetch_po_details() -> list[dict[str, Any]]:
    return _query("po_details", "SELECT * FROM po_details")


def invalidate_cache() -> None:
    """Call after loading fresh CSVs via PO_DB/po_loader so the next invoice sees
    the updated master data immediately instead of waiting out the TTL."""
    _cache.clear()


def connection_check() -> tuple[bool, Optional[str]]:
    """Used by /config-status style checks: (ok, error_message)."""
    if not is_configured():
        return False, "POSTGRES_HOST is not set"
    try:
        conn = _get_conn()
        conn.close()
        return True, None
    except Exception as e:
        return False, str(e)
