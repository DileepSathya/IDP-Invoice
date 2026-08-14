"""Persisted HITL email notification settings.

Three trigger modes (chosen from Settings → Notifications page):
- "immediate": send an email when an invoice enters HITL pending (status=1).
- "scheduled_digest": send a summary every `digest_frequency_minutes` while
  pending count > 0 (background loop in hitl_notification_scheduler.py).
- "threshold_only": send once when pending count crosses upward through
  `pending_threshold`; reset when count drops below threshold again.

Recipient addresses are stored here; SMTP credentials live in `.env`.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "hitl_notifications"
DEFAULT_TRIGGER_MODE = "scheduled_digest"
DEFAULT_DIGEST_FREQUENCY_MINUTES = 60
DEFAULT_PENDING_THRESHOLD = 5
MIN_DIGEST_FREQUENCY_MINUTES = 1
MAX_DIGEST_FREQUENCY_MINUTES = 1440  # 24h
MIN_PENDING_THRESHOLD = 1
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _collection():
    return get_db()["notification_settings"]


def _normalize_trigger_mode(value: Any) -> str:
    mode = str(value or "").strip().lower()
    if mode in ("immediate", "scheduled_digest", "threshold_only"):
        return mode
    return DEFAULT_TRIGGER_MODE


def _parse_recipient_emails(value: Any) -> list[str]:
    if isinstance(value, list):
        raw_parts = [str(v).strip() for v in value]
    else:
        raw_parts = re.split(r"[,;\s]+", str(value or ""))
    emails: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        part = part.strip().lower()
        if not part or part in seen:
            continue
        if _EMAIL_RE.match(part):
            emails.append(part)
            seen.add(part)
    return emails


def get_hitl_notification_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as e:
        logger.warning("[hitl_notification_settings] Could not read settings, using defaults: %s", e)
        doc = {}

    trigger_mode = _normalize_trigger_mode(doc.get("trigger_mode"))
    digest_frequency = doc.get("digest_frequency_minutes") or DEFAULT_DIGEST_FREQUENCY_MINUTES
    try:
        digest_frequency = int(digest_frequency)
    except Exception:
        digest_frequency = DEFAULT_DIGEST_FREQUENCY_MINUTES
    digest_frequency = max(
        MIN_DIGEST_FREQUENCY_MINUTES, min(MAX_DIGEST_FREQUENCY_MINUTES, digest_frequency)
    )

    pending_threshold = doc.get("pending_threshold") or DEFAULT_PENDING_THRESHOLD
    try:
        pending_threshold = int(pending_threshold)
    except Exception:
        pending_threshold = DEFAULT_PENDING_THRESHOLD
    pending_threshold = max(MIN_PENDING_THRESHOLD, pending_threshold)

    return {
        "enabled": bool(doc.get("enabled", False)),
        "recipient_emails": _parse_recipient_emails(doc.get("recipient_emails")),
        "trigger_mode": trigger_mode,
        "digest_frequency_minutes": digest_frequency,
        "pending_threshold": pending_threshold,
        "last_sent_at": doc.get("last_sent_at"),
        "last_pending_count": int(doc.get("last_pending_count") or 0),
        "threshold_alert_active": bool(doc.get("threshold_alert_active", False)),
        "settings_updated_at": doc.get("settings_updated_at"),
    }


def save_hitl_notification_settings(
    *,
    enabled: bool,
    recipient_emails: list[str],
    trigger_mode: str,
    digest_frequency_minutes: int,
    pending_threshold: int,
) -> dict[str, Any]:
    trigger_mode = _normalize_trigger_mode(trigger_mode)
    if trigger_mode not in ("immediate", "scheduled_digest", "threshold_only"):
        raise ValueError(
            "trigger_mode must be 'immediate', 'scheduled_digest', or 'threshold_only'"
        )

    emails = _parse_recipient_emails(recipient_emails)
    if enabled and not emails:
        raise ValueError("At least one valid recipient email is required when notifications are enabled")

    try:
        digest_frequency_minutes = int(digest_frequency_minutes)
    except Exception as e:
        raise ValueError("digest_frequency_minutes must be a whole number") from e
    if (
        digest_frequency_minutes < MIN_DIGEST_FREQUENCY_MINUTES
        or digest_frequency_minutes > MAX_DIGEST_FREQUENCY_MINUTES
    ):
        raise ValueError(
            f"digest_frequency_minutes must be between "
            f"{MIN_DIGEST_FREQUENCY_MINUTES} and {MAX_DIGEST_FREQUENCY_MINUTES}"
        )

    try:
        pending_threshold = int(pending_threshold)
    except Exception as e:
        raise ValueError("pending_threshold must be a whole number") from e
    if pending_threshold < MIN_PENDING_THRESHOLD:
        raise ValueError(f"pending_threshold must be at least {MIN_PENDING_THRESHOLD}")

    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "enabled": bool(enabled),
                "recipient_emails": emails,
                "trigger_mode": trigger_mode,
                "digest_frequency_minutes": digest_frequency_minutes,
                "pending_threshold": pending_threshold,
                "settings_updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info(
        "[hitl_notification_settings] Saved: enabled=%s mode=%s recipients=%s",
        enabled,
        trigger_mode,
        len(emails),
    )
    return get_hitl_notification_settings()


def mark_notification_sent(*, pending_count: int, threshold_alert_active: Optional[bool] = None) -> None:
    update: dict[str, Any] = {
        "last_sent_at": datetime.utcnow(),
        "last_pending_count": pending_count,
    }
    if threshold_alert_active is not None:
        update["threshold_alert_active"] = threshold_alert_active
    _collection().update_one({"_id": _SETTINGS_DOC_ID}, {"$set": update}, upsert=True)


def set_threshold_alert_active(active: bool) -> None:
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {"$set": {"threshold_alert_active": active}},
        upsert=True,
    )


def next_digest_baseline(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    s = settings or get_hitl_notification_settings()
    candidates = [t for t in (s.get("last_sent_at"), s.get("settings_updated_at")) if t is not None]
    return max(candidates) if candidates else None


def due_for_scheduled_digest(settings: Optional[dict[str, Any]] = None) -> bool:
    s = settings or get_hitl_notification_settings()
    if not s.get("enabled") or s.get("trigger_mode") != "scheduled_digest":
        return False
    baseline = next_digest_baseline(s)
    if baseline is None:
        return True
    elapsed_minutes = (datetime.utcnow() - baseline).total_seconds() / 60.0
    return elapsed_minutes >= s["digest_frequency_minutes"]
