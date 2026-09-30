"""SMTP email delivery for HITL notifications backed by MongoDB settings."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage
from typing import Iterable

logger = logging.getLogger(__name__)


def is_smtp_configured() -> bool:
    from backend.hitl_notification_settings import get_hitl_smtp_settings

    return bool(get_hitl_smtp_settings()["configured"])


def _smtp_settings() -> dict[str, object]:
    from backend.hitl_notification_settings import get_hitl_smtp_credentials

    return get_hitl_smtp_credentials()


def send_email(*, to_addresses: Iterable[str], subject: str, body: str) -> None:
    recipients = [addr.strip() for addr in to_addresses if addr and addr.strip()]
    if not recipients:
        raise RuntimeError("No recipient email addresses configured")

    cfg = _smtp_settings()
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = str(cfg["from_addr"])
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    host = str(cfg["host"])
    port = int(cfg["port"])
    user = str(cfg["user"])
    password = str(cfg["password"])
    use_tls = bool(cfg["use_tls"])

    try:
        with smtplib.SMTP(host, port, timeout=30) as smtp:
            if use_tls:
                smtp.starttls()
            if user:
                smtp.login(user, password)
            smtp.send_message(msg)
    except Exception:
        logger.exception("[hitl_email] Failed to send email to %s", recipients)
        raise

    logger.info("[hitl_email] Sent email to %s: %s", recipients, subject)
