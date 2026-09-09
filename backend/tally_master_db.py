"""MongoDB storage for Tally master data used in ERP matching.

Vendors, items, and purchase orders are pulled from Tally via the bridge
only when the user triggers a refresh (see backend/tally_master_sync.py).
Invoice matching reads exclusively from these collections.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SYNC_DOC_ID = "tally_master_sync"
_COLLECTIONS = (
    "tally_vendor_master",
    "tally_item_master",
    "tally_expense_ledger_master",
    "tally_po_header",
    "tally_po_details",
)

_CACHE_TTL_SECONDS = 60
_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _settings_collection():
    return get_db()["erp_settings"]


def get_sync_metadata() -> dict[str, Any]:
    try:
        doc = _settings_collection().find_one({"_id": _SYNC_DOC_ID}) or {}
    except Exception as exc:
        logger.warning("[tally_master_db] Could not read sync metadata: %s", exc)
        doc = {}

    last_result = doc.get("last_result")
    if not isinstance(last_result, dict):
        last_result = {}

    return {
        "last_synced_at": doc.get("last_synced_at"),
        "syncing": bool(doc.get("syncing", False)),
        "company": doc.get("company"),
        "last_result": last_result,
        "last_error": doc.get("last_error"),
    }


def mark_sync_started() -> bool:
    doc = _settings_collection().find_one({"_id": _SYNC_DOC_ID}) or {}
    if doc.get("syncing"):
        return False
    _settings_collection().update_one(
        {"_id": _SYNC_DOC_ID},
        {
            "$set": {
                "syncing": True,
                "last_error": None,
            }
        },
        upsert=True,
    )
    return True


def mark_sync_finished(
    *,
    company: Optional[str],
    counts: dict[str, int],
    errors: list[str],
    success: bool,
) -> None:
    last_result = {
        "vendors": counts.get("vendors", 0),
        "items": counts.get("items", 0),
        "expense_ledgers": counts.get("expense_ledgers", 0),
        "po_headers": counts.get("po_headers", 0),
        "po_lines": counts.get("po_lines", 0),
        "errors": errors,
        "success": success,
    }
    _settings_collection().update_one(
        {"_id": _SYNC_DOC_ID},
        {
            "$set": {
                "syncing": False,
                "last_synced_at": datetime.utcnow(),
                "company": company,
                "last_result": last_result,
                "last_error": "; ".join(errors) if errors else None,
            }
        },
        upsert=True,
    )


def has_master_data() -> bool:
    try:
        db = get_db()
        for name in _COLLECTIONS:
            if db[name].estimated_document_count() > 0:
                return True
    except Exception as exc:
        logger.warning("[tally_master_db] Could not check master data: %s", exc)
    return False


def is_configured() -> bool:
    """True when Tally master data has been loaded into MongoDB at least once."""
    from backend.tally_integration.config import is_tally_configured

    return is_tally_configured() and has_master_data()


def invalidate_cache() -> None:
    _cache.clear()


def _fetch_collection(collection_name: str) -> list[dict[str, Any]]:
    now = time.monotonic()
    cached = _cache.get(collection_name)
    if cached and now - cached[0] < _CACHE_TTL_SECONDS:
        return cached[1]

    try:
        rows = list(get_db()[collection_name].find({}, {"_id": 0}))
    except Exception as exc:
        logger.warning("[tally_master_db] Read failed for %s: %s", collection_name, exc)
        return cached[1] if cached else []

    _cache[collection_name] = (now, rows)
    return rows


def fetch_vendor_master() -> list[dict[str, Any]]:
    return _fetch_collection("tally_vendor_master")


def fetch_item_master() -> list[dict[str, Any]]:
    return _fetch_collection("tally_item_master")


def search_master(kind: str, query: str = "", limit: int = 20) -> list[dict[str, str]]:
    """Rank exact names, phrases, then unordered partial words; preserve labels."""
    sources = {
        "items": (fetch_item_master, "item_name", "item_id", ("units", "category")),
        "vendors": (fetch_vendor_master, "vendor_name", "vendor_id", ("gst_tax_number",)),
        "ledgers": (fetch_expense_ledger_master, "ledger_name", "ledger_id", ()),
    }
    if kind not in sources:
        raise ValueError("Unknown master type")
    fetch, name_key, id_key, details = sources[kind]
    rows = fetch()
    query = query.strip().casefold()
    def normalize(text: str) -> str:
        return " ".join(re.findall(r"[^\W_]+", text.casefold()))

    normalized_query = normalize(query)
    tokens = normalized_query.split()
    results = []
    for row in rows:
        name = str(row.get(name_key) or "")
        if not name.strip():
            continue
        detail = " · ".join(str(row[key]) for key in details if row.get(key))
        searchable = f"{name} {detail} {row.get('description') or ''}".casefold()
        normalized_name = normalize(name)
        if tokens:
            words = normalize(searchable).split()
            if not all(any(word.startswith(token) for word in words) for token in tokens):
                continue
            if normalized_name == normalized_query:
                rank = 0
            elif normalized_name.startswith(normalized_query):
                rank = 1
            elif f" {normalized_query} " in f" {normalized_name} ":
                rank = 2
            elif all(any(word.startswith(token) for word in normalized_name.split()) for token in tokens):
                rank = 3
            else:
                rank = 4  # Supplementary fields match after names.
        else:
            # Empty input browses; punctuation-only queries remain literal.
            if query and query not in searchable:
                continue
            rank = 0
        results.append((rank, name.casefold(), {
            "id": str(row.get(id_key) or ""),
            "name": name,
            "detail": detail,
        }))
    results.sort(key=lambda entry: (entry[0], entry[1]))
    return [entry[2] for entry in results[:max(1, min(limit, 50))]]


def fetch_expense_ledger_master() -> list[dict[str, Any]]:
    return _fetch_collection("tally_expense_ledger_master")


def fetch_po_header() -> list[dict[str, Any]]:
    return _fetch_collection("tally_po_header")


def fetch_po_details() -> list[dict[str, Any]]:
    return _fetch_collection("tally_po_details")


def replace_master_data(
    *,
    company: Optional[str],
    vendors: list[dict[str, Any]],
    items: list[dict[str, Any]],
    expense_ledgers: list[dict[str, Any]],
    po_headers: list[dict[str, Any]],
    po_details: list[dict[str, Any]],
) -> dict[str, int]:
    """Replace all master collections atomically (best-effort per collection)."""
    db = get_db()
    synced_at = datetime.utcnow()
    company_value = (company or "").strip() or None

    payloads = (
        ("tally_vendor_master", vendors),
        ("tally_item_master", items),
        ("tally_expense_ledger_master", expense_ledgers),
        ("tally_po_header", po_headers),
        ("tally_po_details", po_details),
    )

    counts: dict[str, int] = {}
    for collection_name, rows in payloads:
        coll = db[collection_name]
        coll.delete_many({})
        if rows:
            stamped = []
            for row in rows:
                doc = dict(row)
                doc["synced_at"] = synced_at
                if company_value:
                    doc.setdefault("company", company_value)
                stamped.append(doc)
            coll.insert_many(stamped)
        counts[collection_name] = len(rows)

    invalidate_cache()

    return {
        "vendors": counts["tally_vendor_master"],
        "items": counts["tally_item_master"],
        "expense_ledgers": counts["tally_expense_ledger_master"],
        "po_headers": counts["tally_po_header"],
        "po_lines": counts["tally_po_details"],
    }
