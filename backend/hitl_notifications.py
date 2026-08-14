"""HITL notification triggers and email content."""

from __future__ import annotations

import logging
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


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "y")


def count_hitl_pending() -> int:
    coll = get_invoices_collection()
    pending = 0
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
        if status_value == 1:
            pending += 1
    return pending


def _format_timestamp() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def _build_email_body(*, pending_count: int, context: str) -> str:
    return (
        f"HITL Notification — {context}\n\n"
        f"Timestamp: {_format_timestamp()}\n"
        f"Invoices pending human review (HITL): {pending_count}\n"
    )


def _try_send(*, settings: dict[str, Any], subject: str, body: str, pending_count: int, threshold_alert_active: Optional[bool] = None) -> bool:
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


def on_hitl_status_change(*, previous_status: Optional[int], new_status: int) -> None:
    """Call after HITL lifecycle status is computed for an invoice."""
    settings = get_hitl_notification_settings()
    if not settings.get("enabled"):
        return

    pending_count = count_hitl_pending()
    mode = settings.get("trigger_mode")

    if mode == "immediate" and new_status == 1 and previous_status != 1:
        body = _build_email_body(pending_count=pending_count, context="New invoice flagged for review")
        _try_send(
            settings=settings,
            subject=f"[IDP] HITL alert — {pending_count} pending",
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
        body = _build_email_body(
            pending_count=pending_count,
            context=f"Threshold reached ({threshold}+ invoices pending review)",
        )
        _try_send(
            settings=settings,
            subject=f"[IDP] HITL threshold alert — {pending_count} pending",
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

    pending_count = count_hitl_pending()
    if pending_count <= 0:
        return

    body = _build_email_body(pending_count=pending_count, context="Scheduled digest")
    _try_send(
        settings=settings,
        subject=f"[IDP] HITL digest — {pending_count} pending",
        body=body,
        pending_count=pending_count,
    )


def send_test_notification() -> None:
    settings = get_hitl_notification_settings()
    if not settings.get("recipient_emails"):
        raise RuntimeError("Add at least one recipient email before sending a test")
    if not is_smtp_configured():
        raise RuntimeError("SMTP is not configured — set SMTP_HOST and SMTP_FROM in .env")

    pending_count = count_hitl_pending()
    body = (
        _build_email_body(pending_count=pending_count, context="Test email")
        + "\nThis is a test message from IDP HITL notification settings.\n"
    )
    send_email(
        to_addresses=settings["recipient_emails"],
        subject="[IDP] HITL notification test",
        body=body,
    )
