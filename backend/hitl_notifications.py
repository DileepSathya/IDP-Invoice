"""HITL notification triggers and email content."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any, Optional

from backend.agents.database import get_invoices_collection
from backend.hitl_email import is_smtp_configured, send_email
from backend.hitl_notification_settings import (
    due_for_scheduled_digest,
    get_hitl_notification_settings,
    mark_notification_sent,
    set_threshold_alert_active,
)

logger = logging.getLogger(__name__)


def _format_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _hitl_review_url() -> str:
    base = (os.getenv("IDP_APP_URL") or "http://localhost:8000").strip().rstrip("/")
    return f"{base}/?hitl=1"


def _invoice_number(gemini_json: dict[str, Any]) -> str:
    for key in ("invoice_number", "invoice", "Invoice_Number", "InvoiceNumber"):
        value = gemini_json.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "Unknown"


def _hitl_reasons(additional_fields: dict[str, Any]) -> list[str]:
    remarks = additional_fields.get("hitl_remarks")
    if isinstance(remarks, list):
        cleaned = [str(r).strip() for r in remarks if str(r).strip()]
        if cleaned:
            return cleaned
    remark = additional_fields.get("hitl_remark")
    if isinstance(remark, str) and remark.strip():
        return [part.strip() for part in remark.split(";") if part.strip()]
    return []


def _recorded_invoice_summary(gemini_json: dict[str, Any]) -> str:
    additional = gemini_json.get("additional_fields") or {}
    if not isinstance(additional, dict):
        additional = {}

    lines: list[str] = []
    seller = gemini_json.get("seller")
    if seller not in (None, ""):
        lines.append(f"Seller: {seller}")

    invoice_date = gemini_json.get("invoice_date")
    if invoice_date not in (None, ""):
        lines.append(f"Invoice date: {invoice_date}")

    total_amount = gemini_json.get("total_amount") or gemini_json.get("grand_total")
    if total_amount not in (None, ""):
        lines.append(f"Total amount: {total_amount}")

    po_id = gemini_json.get("po_id") or additional.get("po_id")
    if po_id not in (None, ""):
        lines.append(f"PO ID: {po_id}")

    due_date = gemini_json.get("due_date")
    if due_date not in (None, ""):
        lines.append(f"Due date: {due_date}")

    return "\n".join(lines) if lines else "(no extracted invoice values available)"


def _format_reasons_text(reasons: list[str]) -> str:
    if not reasons:
        return "(none recorded)"
    return "\n".join(f"- {reason}" for reason in reasons)


def _format_pending_table_rows(pending_invoices: list[dict[str, Any]]) -> str:
    if not pending_invoices:
        return "(no invoices pending review)"

    number_width = max(len("Invoice Number"), *(len(row["invoice_number"]) for row in pending_invoices))
    reason_header = "Reason(s) for Human Review"
    lines = [
        f"{'Invoice Number'.ljust(number_width)} | {reason_header}",
        f"{'-' * number_width}-+-{'-' * len(reason_header)}",
    ]
    for row in pending_invoices:
        reasons = row.get("reasons") or []
        reason_text = "; ".join(reasons) if reasons else "(none recorded)"
        lines.append(f"{row['invoice_number'].ljust(number_width)} | {reason_text}")
    return "\n".join(lines)


def _build_immediate_email_body(
    *,
    invoice_number: str,
    gemini_json: dict[str, Any],
    reasons: list[str],
) -> str:
    review_url = _hitl_review_url()
    return (
        "HITL Notification — New invoice flagged for review\n\n"
        f"Timestamp: {_format_timestamp()}\n"
        f"Invoice number: {invoice_number}\n\n"
        "Recorded value of invoice:\n"
        f"{_recorded_invoice_summary(gemini_json)}\n\n"
        "Reason(s) for human review:\n"
        f"{_format_reasons_text(reasons)}\n\n"
        "Click here to go to the HITL review page:\n"
        f"{review_url}\n"
    )


def _build_digest_email_body(*, pending_invoices: list[dict[str, Any]]) -> str:
    review_url = _hitl_review_url()
    pending_count = len(pending_invoices)
    return (
        "HITL Notification — Scheduled alert\n\n"
        f"Timestamp: {_format_timestamp()}\n"
        f"Count of HITL flagged invoices: {pending_count}\n\n"
        "Click here to go to the HITL review page:\n"
        f"{review_url}\n\n"
        f"{_format_pending_table_rows(pending_invoices)}\n"
    )


def _pending_hitl_invoices() -> list[dict[str, Any]]:
    coll = get_invoices_collection()
    pending: list[dict[str, Any]] = []
    for doc in coll.find({}):
        gemini_json = (doc.get("gemini") or {}).get("json") or {}
        if not isinstance(gemini_json, dict):
            continue
        additional = gemini_json.get("additional_fields") or {}
        if not isinstance(additional, dict):
            continue
        status_raw = additional.get("status")
        try:
            status_value = int(status_raw) if status_raw is not None else None
        except Exception:
            status_value = None
        if status_value != 1:
            continue
        pending.append(
            {
                "invoice_id": str(doc.get("_id") or ""),
                "invoice_number": _invoice_number(gemini_json),
                "reasons": _hitl_reasons(additional),
                "gemini_json": gemini_json,
            }
        )
    pending.sort(key=lambda row: row["invoice_number"].lower())
    return pending


def count_hitl_pending() -> int:
    return len(_pending_hitl_invoices())


def _try_send(
    *,
    settings: dict[str, Any],
    subject: str,
    body: str,
    pending_count: int,
    threshold_alert_active: Optional[bool] = None,
) -> bool:
    if not settings.get("enabled"):
        return False
    if not is_smtp_configured():
        logger.warning("[hitl_notifications] SMTP is not configured; skipping email")
        return False
    recipients = settings.get("recipient_emails") or []
    if not recipients:
        logger.warning("[hitl_notifications] No recipients configured; skipping email")
        return False
    try:
        send_email(to_addresses=recipients, subject=subject, body=body)
    except Exception:
        return False
    mark_notification_sent(pending_count=pending_count, threshold_alert_active=threshold_alert_active)
    return True


def on_hitl_status_change(
    *,
    previous_status: Optional[int],
    new_status: int,
    gemini_json: Optional[dict[str, Any]] = None,
) -> None:
    """Call after HITL lifecycle status is computed for an invoice."""
    settings = get_hitl_notification_settings()
    if not settings.get("enabled"):
        return

    pending_count = count_hitl_pending()
    mode = settings.get("trigger_mode")

    if mode == "immediate" and new_status == 1 and previous_status != 1:
        invoice_json = gemini_json if isinstance(gemini_json, dict) else {}
        additional = invoice_json.get("additional_fields") or {}
        if not isinstance(additional, dict):
            additional = {}
        invoice_number = _invoice_number(invoice_json)
        reasons = _hitl_reasons(additional)
        body = _build_immediate_email_body(
            invoice_number=invoice_number,
            gemini_json=invoice_json,
            reasons=reasons,
        )
        _try_send(
            settings=settings,
            subject=f"IDP-HITL Alert - Invoice no: {invoice_number}",
            body=body,
            pending_count=pending_count,
        )
        return

    if mode == "threshold_only":
        _maybe_send_threshold_alert(settings=settings, pending_count=pending_count)


def _maybe_send_threshold_alert(*, settings: dict[str, Any], pending_count: int) -> None:
    threshold = int(settings.get("pending_threshold") or 1)
    alert_active = bool(settings.get("threshold_alert_active"))

    if pending_count >= threshold and not alert_active:
        pending_invoices = _pending_hitl_invoices()
        body = _build_digest_email_body(pending_invoices=pending_invoices)
        _try_send(
            settings=settings,
            subject="IDP-HITL Scheduled Alert",
            body=body,
            pending_count=pending_count,
            threshold_alert_active=True,
        )
    elif pending_count < threshold and alert_active:
        set_threshold_alert_active(False)


def maybe_send_scheduled_digest() -> None:
    settings = get_hitl_notification_settings()
    if not settings.get("enabled") or settings.get("trigger_mode") != "scheduled_digest":
        return
    if not due_for_scheduled_digest(settings):
        return

    pending_invoices = _pending_hitl_invoices()
    if not pending_invoices:
        return

    body = _build_digest_email_body(pending_invoices=pending_invoices)
    _try_send(
        settings=settings,
        subject="IDP-HITL Scheduled Alert",
        body=body,
        pending_count=len(pending_invoices),
    )


def send_test_notification() -> None:
    settings = get_hitl_notification_settings()
    if not settings.get("recipient_emails"):
        raise RuntimeError("Add at least one recipient email before sending a test")
    if not is_smtp_configured():
        raise RuntimeError("SMTP is not configured — set SMTP_HOST and SMTP_FROM in .env")

    mode = settings.get("trigger_mode")
    pending_invoices = _pending_hitl_invoices()

    if mode == "immediate":
        sample_json = (
            pending_invoices[0]["gemini_json"]
            if pending_invoices
            else {
                "invoice_number": "SAMPLE-INV-001",
                "seller": "Sample Vendor Pvt Ltd",
                "invoice_date": "2026-08-18",
                "total_amount": "11800.00",
                "additional_fields": {
                    "hitl_remarks": [
                        "Total amount mismatch",
                        "Vendor not matched in ERP master data",
                    ]
                },
            }
        )
        additional = sample_json.get("additional_fields") or {}
        if not isinstance(additional, dict):
            additional = {}
        invoice_number = _invoice_number(sample_json)
        body = (
            _build_immediate_email_body(
                invoice_number=invoice_number,
                gemini_json=sample_json,
                reasons=_hitl_reasons(additional)
                or ["Total amount mismatch", "Vendor not matched in ERP master data"],
            )
            + "\n(This is a test message from IDP HITL notification settings.)\n"
        )
        subject = f"IDP-HITL Alert - Invoice no: {invoice_number}"
    else:
        sample_rows = pending_invoices or [
            {
                "invoice_number": "SAMPLE-INV-001",
                "reasons": ["Total amount mismatch"],
            },
            {
                "invoice_number": "SAMPLE-INV-002",
                "reasons": ["PO ID missing", "Vendor not matched in ERP master data"],
            },
        ]
        body = (
            _build_digest_email_body(pending_invoices=sample_rows)
            + "\n(This is a test message from IDP HITL notification settings.)\n"
        )
        subject = "IDP-HITL Scheduled Alert"

    send_email(
        to_addresses=settings["recipient_emails"],
        subject=subject,
        body=body,
    )
