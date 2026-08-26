"""Persisted ERP sync settings.

Two sync modes, chosen from the ERP page:
- "immediate" (default): ERP matching runs synchronously when an invoice is first
  processed, and again on every later edit.
- "scheduled": edits defer re-matching until the next scheduled sync (re-matches
  all invoices using cached Tally master data in MongoDB).

Settings are stored in MongoDB as a single document so the API process and
its background scheduler thread always agree on the current mode/frequency,
and so a "Force Sync" click from any request is immediately visible to the
scheduler loop (no restart needed).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "erp_sync"
DEFAULT_MODE = "immediate"
DEFAULT_FREQUENCY_MINUTES = 15
MIN_FREQUENCY_MINUTES = 1
MAX_FREQUENCY_MINUTES = 1440  # 24h


def _collection():
    return get_db()["erp_settings"]


def get_erp_sync_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as e:
        logger.warning("[erp_settings] Could not read settings, using defaults: %s", e)
        doc = {}

    mode = doc.get("mode") or DEFAULT_MODE
    if mode not in ("immediate", "scheduled"):
        mode = DEFAULT_MODE

    frequency = doc.get("frequency_minutes") or DEFAULT_FREQUENCY_MINUTES
    try:
        frequency = int(frequency)
    except Exception:
        frequency = DEFAULT_FREQUENCY_MINUTES
    frequency = max(MIN_FREQUENCY_MINUTES, min(MAX_FREQUENCY_MINUTES, frequency))

    return {
        "mode": mode,
        "frequency_minutes": frequency,
        "last_synced_at": doc.get("last_synced_at"),
        "last_sync_result": doc.get("last_sync_result"),
        "syncing": bool(doc.get("syncing", False)),
        "settings_updated_at": doc.get("settings_updated_at"),
    }


def save_erp_sync_settings(*, mode: str, frequency_minutes: int) -> dict[str, Any]:
    mode = (mode or "").strip().lower()
    if mode not in ("immediate", "scheduled"):
        raise ValueError("mode must be 'immediate' or 'scheduled'")

    try:
        frequency_minutes = int(frequency_minutes)
    except Exception as e:
        raise ValueError("frequency_minutes must be a whole number") from e
    if frequency_minutes < MIN_FREQUENCY_MINUTES or frequency_minutes > MAX_FREQUENCY_MINUTES:
        raise ValueError(
            f"frequency_minutes must be between {MIN_FREQUENCY_MINUTES} and {MAX_FREQUENCY_MINUTES}"
        )

    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "mode": mode,
                "frequency_minutes": frequency_minutes,
                # Anchors the "next sync" countdown to the moment settings were saved
                # rather than whenever the last sync happened to complete - otherwise
                # shortening the frequency (e.g. 15min -> 2min) would make the next sync
                # look like it was already overdue in the past, instead of counting down
                # from now. See next_sync_baseline().
                "settings_updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info(
        "[erp_settings] Saved sync settings: mode=%s frequency_minutes=%s", mode, frequency_minutes
    )
    return get_erp_sync_settings()


def mark_sync_started() -> bool:
    """Best-effort lock: only one sync (scheduled tick or Force Sync) runs at a
    time. Returns False without changing anything if a sync is already in
    progress."""
    result = _collection().update_one(
        {"_id": _SETTINGS_DOC_ID, "syncing": {"$ne": True}},
        {"$set": {"syncing": True}},
        upsert=False,
    )
    if result.matched_count == 0:
        # Either already syncing, or the settings doc doesn't exist yet.
        existing = _collection().find_one({"_id": _SETTINGS_DOC_ID})
        if existing is None:
            _collection().update_one(
                {"_id": _SETTINGS_DOC_ID}, {"$set": {"syncing": True}}, upsert=True
            )
            return True
        return not bool(existing.get("syncing"))
    return True


def mark_sync_finished(*, scanned: int, updated: int, errored: int) -> None:
    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {
            "$set": {
                "syncing": False,
                "last_synced_at": datetime.utcnow(),
                "last_sync_result": {"scanned": scanned, "updated": updated, "errored": errored},
            }
        },
        upsert=True,
    )


def should_match_immediately(settings: Optional[dict[str, Any]] = None) -> bool:
    """False when mode=="scheduled" - edits to an already-stored invoice should then
    wait for the next scheduled sync (or a manual Force Sync) instead of hitting
    Tally master data synchronously on every keystroke/save. True for "immediate" mode, which
    is also the safe default if settings can't be read for some reason."""
    s = settings or get_erp_sync_settings()
    return s.get("mode") != "scheduled"


def next_sync_baseline(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    """The timestamp the next-sync countdown should be measured from: whichever is more
    recent of the last completed sync or the last settings save. Saving new settings
    (e.g. shortening the frequency) resets the countdown to start from that moment
    instead of leaving it measured against an old last_synced_at, which could already
    be further in the past than the new (shorter) frequency - making "next sync" look
    like it was already due, instead of counting down from when you actually saved."""
    s = settings or get_erp_sync_settings()
    candidates = [t for t in (s.get("last_synced_at"), s.get("settings_updated_at")) if t is not None]
    return max(candidates) if candidates else None


def due_for_scheduled_sync(settings: Optional[dict[str, Any]] = None) -> bool:
    """True when mode=="scheduled" and either no sync/settings-save has happened yet,
    or the configured frequency has elapsed since the more recent of those two
    (see next_sync_baseline)."""
    s = settings or get_erp_sync_settings()
    if s["mode"] != "scheduled" or s.get("syncing"):
        return False
    baseline = next_sync_baseline(s)
    if baseline is None:
        return True
    elapsed_minutes = (datetime.utcnow() - baseline).total_seconds() / 60.0
    return elapsed_minutes >= s["frequency_minutes"]
