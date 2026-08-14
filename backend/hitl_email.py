"""SMTP email delivery for HITL notifications.

Credentials and server settings are read from environment variables so secrets
stay out of MongoDB. Recipient addresses come from notification settings.
"""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from typing import Iterable

logger = logging.getLogger(__name__)


def is_smtp_configured() -> bool:
    host = (os.getenv("SMTP_HOST") or "").strip()
    from_addr = (os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "").strip()
    return bool(host and from_addr)


def _smtp_settings() -> dict[str, object]:
    host = (os.getenv("SMTP_HOST") or "").strip()
    if not host:
        raise RuntimeError("SMTP_HOST is not set in .env")

    port_raw = (os.getenv("SMTP_PORT") or "587").strip()
    try:
        port = int(port_raw)
    except ValueError as e:
        raise RuntimeError("SMTP_PORT must be a whole number") from e

    user = (os.getenv("SMTP_USER") or "").strip()
    password = os.getenv("SMTP_PASSWORD") or ""
    from_addr = (os.getenv("SMTP_FROM") or user or "").strip()
    if not from_addr:
        raise RuntimeError("SMTP_FROM or SMTP_USER must be set in .env")

    use_tls_raw = (os.getenv("SMTP_USE_TLS") or "true").strip().lower()
    use_tls = use_tls_raw not in ("0", "false", "no")

    return {
        "host": host,
        "port": port,
        "user": user,
        "password": password,
        "from_addr": from_addr,
        "use_tls": use_tls,
    }


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
