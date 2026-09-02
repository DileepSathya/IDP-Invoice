"""ERP reference data for invoice matching (Tally master data in MongoDB).

Master data is loaded from Tally via Settings → Tally Master Data refresh and stored
in MongoDB collections (see backend/tally_master_db.py). Invoice matching reads
only from MongoDB — Tally is not contacted per invoice.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from backend.app_paths import load_app_dotenv
from backend import tally_master_db


def is_configured() -> bool:
    return tally_master_db.is_configured()


def get_match_threshold() -> float:
    load_app_dotenv()
    try:
        return float(os.environ.get("ERP_MATCH_THRESHOLD", "90"))
    except (TypeError, ValueError):
        return 90.0


def fetch_vendor_master() -> list[dict[str, Any]]:
    return tally_master_db.fetch_vendor_master()


def fetch_item_master() -> list[dict[str, Any]]:
    return tally_master_db.fetch_item_master()


def fetch_expense_ledger_master() -> list[dict[str, Any]]:
    return tally_master_db.fetch_expense_ledger_master()


def fetch_po_header() -> list[dict[str, Any]]:
    return tally_master_db.fetch_po_header()


def fetch_po_details() -> list[dict[str, Any]]:
    return tally_master_db.fetch_po_details()


def invalidate_cache() -> None:
    tally_master_db.invalidate_cache()


def connection_check() -> tuple[bool, Optional[str]]:
    from backend.tally_integration.bridge_client import ping_bridge
    from backend.tally_integration.config import is_tally_configured

    if not is_tally_configured():
        return False, "Tally is not configured (set TALLY_ENABLED=true and TALLY_BRIDGE_URL)"

    if not tally_master_db.has_master_data():
        return False, "Tally master data not loaded — refresh from Settings → Tally Master Data"

    reachable, err = ping_bridge()
    if not reachable:
        return False, err or "Tally bridge is not reachable"
    return True, None


def erp_source_label() -> str:
    return "Tally (MongoDB)"
