"""Pull Tally master data via the bridge and persist it in MongoDB."""

from __future__ import annotations

import logging
import threading
from typing import Any

from backend import tally_master_db
from backend.tally_integration.bridge_client import fetch_all_masters_from_bridge

logger = logging.getLogger(__name__)

_refresh_thread: threading.Thread | None = None
_refresh_lock = threading.Lock()


def run_tally_master_refresh(*, rematch_invoices: bool = False) -> dict[str, Any]:
    """Synchronously refresh Tally master data in MongoDB."""
    from backend.tally_integration.config import is_tally_configured

    if not is_tally_configured():
        return {
            "success": False,
            "skipped_reason": "Tally is not configured",
            "counts": {},
            "errors": ["Set TALLY_ENABLED=true and TALLY_BRIDGE_URL in .env"],
        }

    if not tally_master_db.mark_sync_started():
        return {
            "success": False,
            "skipped_reason": "Refresh already in progress",
            "counts": {},
            "errors": [],
        }

    errors: list[str] = []
    counts: dict[str, int] = {}
    company: str | None = None
    success = False

    try:
        payload = fetch_all_masters_from_bridge()
        company = payload.get("company")
        errors = list(payload.get("errors") or [])
        bridge_error = payload.get("error")
        if bridge_error:
            errors.append(str(bridge_error))

        vendors = payload.get("vendors") or []
        items = payload.get("items") or []
        expense_ledgers = payload.get("expense_ledgers") or []
        po_headers = payload.get("po_headers") or []
        po_details = payload.get("po_details") or []

        if not errors or (vendors or items or expense_ledgers or po_headers):
            counts = tally_master_db.replace_master_data(
                company=company,
                vendors=vendors,
                items=items,
                expense_ledgers=expense_ledgers,
                po_headers=po_headers,
                po_details=po_details,
            )
            success = not errors
        else:
            success = False
    except Exception as exc:
        logger.exception("[tally_master_sync] Refresh failed: %s", exc)
        errors.append(str(exc))
        success = False
    finally:
        tally_master_db.mark_sync_finished(
            company=company,
            counts=counts,
            errors=errors,
            success=success,
        )

    if success and rematch_invoices:
        from backend.erp_sync import run_erp_sync_async

        run_erp_sync_async()

    return {
        "success": success,
        "company": company,
        "counts": counts,
        "errors": errors,
        "metadata": tally_master_db.get_sync_metadata(),
    }


def run_tally_master_refresh_async(*, rematch_invoices: bool = False) -> bool:
    """Fire-and-forget master refresh."""
    global _refresh_thread

    meta = tally_master_db.get_sync_metadata()
    if meta.get("syncing"):
        return False

    def _run() -> None:
        try:
            run_tally_master_refresh(rematch_invoices=rematch_invoices)
        except Exception:
            logger.exception("[tally_master_sync] Background refresh failed")

    with _refresh_lock:
        if _refresh_thread is not None and _refresh_thread.is_alive():
            return False
        _refresh_thread = threading.Thread(
            target=_run,
            name="tally-master-refresh",
            daemon=True,
        )
        _refresh_thread.start()
    return True
