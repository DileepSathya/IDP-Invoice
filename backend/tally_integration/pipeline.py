"""Persist Tally push results and call the TALLY INTEGRATION bridge."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from backend.tally_integration import config
from backend.tally_integration.bridge_client import TallyPushResult, push_invoice_via_bridge

log = logging.getLogger(__name__)

__all__ = [
    "TallyPushResult",
    "display_erp_remark",
    "invoice_already_pushed",
    "persist_tally_result",
    "push_invoice_to_tally",
]


def display_erp_remark(
    additional_fields: dict[str, Any],
    *,
    erp_matching_complete: bool,
) -> Optional[str]:
    stored = additional_fields.get(config.ERP_REMARK_KEY)
    if stored:
        return str(stored)

    status = additional_fields.get(config.TALLY_PUSH_STATUS_KEY)
    if status == "success":
        return "Successful"
    if status == "failed":
        err = additional_fields.get(config.TALLY_ERROR_REASON_KEY) or "Push failed"
        return f"Unsuccessful: {str(err)[:120]}"

    if not erp_matching_complete:
        return None
    if not config.is_tally_configured():
        return "Tally not configured"
    return "Pending Tally push"


def invoice_already_pushed(additional_fields: dict[str, Any]) -> bool:
    return additional_fields.get(config.TALLY_PUSH_STATUS_KEY) == "success"


def persist_tally_result(coll: Any, doc_id: Any, result: TallyPushResult) -> None:
    status = "skipped" if result.skipped else ("success" if result.success else "failed")
    update: dict[str, Any] = {
        f"gemini.json.additional_fields.{config.TALLY_PUSH_STATUS_KEY}": status,
        f"gemini.json.additional_fields.{config.ERP_REMARK_KEY}": result.erp_remark,
        f"gemini.json.additional_fields.{config.TALLY_ERROR_REASON_KEY}": result.error_reason,
        f"gemini.json.additional_fields.{config.TALLY_PUSHED_AT_KEY}": result.pushed_at.isoformat(),
    }
    inc: dict[str, int] = {}
    if not result.skipped:
        inc[f"gemini.json.additional_fields.{config.TALLY_PUSH_ATTEMPTS_KEY}"] = 1

    try:
        if inc:
            coll.update_one({"_id": doc_id}, {"$set": update, "$inc": inc})
        else:
            coll.update_one({"_id": doc_id}, {"$set": update})
    except Exception as exc:
        log.warning("[tally] Could not persist result for _id=%s: %s", doc_id, exc)


def push_invoice_to_tally(doc: dict[str, Any], *, force: bool = False) -> TallyPushResult:
    doc_id = doc.get("_id")
    invoice_id = str(doc_id) if doc_id is not None else ""
    now = datetime.now(timezone.utc)

    raw_g = (doc.get("gemini") or {}).get("json") or {}
    if not isinstance(raw_g, dict):
        raw_g = {}
    additional_fields = raw_g.get("additional_fields")
    if not isinstance(additional_fields, dict):
        additional_fields = {}

    blocked, block_reason = config.po_blocks_tally_push(raw_g)
    if blocked:
        inv_no = str(raw_g.get("invoice_number") or raw_g.get("invoice") or "")
        return TallyPushResult(
            success=False,
            invoice_id=invoice_id,
            invoice_number=inv_no,
            message="Tally push blocked",
            error_reason=block_reason,
            tally_company=None,
            pushed_at=now,
            skipped=True,
        )

    if not config.is_mandatory_purchase_order():
        previous_po = raw_g.get("po_id")
        resolved_po = config.resolve_po_id(raw_g)
        if resolved_po and resolved_po != previous_po and doc_id is not None:
            try:
                from backend.agents.database import get_invoices_collection

                get_invoices_collection().update_one(
                    {"_id": doc_id},
                    {"$set": {"gemini.json.po_id": resolved_po}},
                )
            except Exception as exc:
                log.warning("[tally] Could not persist optional PO value for _id=%s: %s", doc_id, exc)

    if not config.is_tally_configured():
        return TallyPushResult(
            success=False,
            invoice_id=invoice_id,
            invoice_number="",
            message="Tally not configured",
            error_reason="Set TALLY_ENABLED=true and TALLY_BRIDGE_URL in .env",
            tally_company=None,
            pushed_at=now,
            skipped=True,
        )

    if not force and invoice_already_pushed(additional_fields):
        inv_no = str(
            (raw_g.get("invoice_number") or raw_g.get("invoice") or "") if isinstance(raw_g, dict) else ""
        )
        return TallyPushResult(
            success=True,
            invoice_id=invoice_id,
            invoice_number=inv_no,
            message="Already pushed to Tally",
            error_reason=None,
            tally_company=None,
            pushed_at=now,
            skipped=True,
        )

    if not invoice_id:
        return TallyPushResult(
            success=False,
            invoice_id="",
            invoice_number="",
            message="Missing invoice id",
            error_reason="MongoDB document has no _id",
            tally_company=None,
            pushed_at=now,
        )

    return push_invoice_via_bridge(invoice_id, force=force)
