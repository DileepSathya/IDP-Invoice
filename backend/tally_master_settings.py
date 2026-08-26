"""Persisted Tally master-data refresh scheduler settings.

Two modes, configured from Settings → Tally Master Data:
- "manual" (default): master data is refreshed only via the Refresh button.
- "scheduled": the background scheduler pulls vendors/items/POs from Tally every
  N minutes. The manual Refresh button remains available alongside the scheduler.

Settings live in MongoDB (erp_settings collection) so the API and background
scheduler thread always agree on the current mode/frequency.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Optional

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "tally_master_scheduler"
DEFAULT_MODE = "manual"
DEFAULT_FREQUENCY_MINUTES = 60
MIN_FREQUENCY_MINUTES = 1
MAX_FREQUENCY_MINUTES = 1440  # 24h


def _collection():
    return get_db()["erp_settings"]


def get_tally_master_scheduler_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as exc:
        logger.warning("[tally_master_settings] Could not read settings, using defaults: %s", exc)
        doc = {}

    mode = doc.get("mode") or DEFAULT_MODE
    if mode not in ("manual", "scheduled"):
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
        "rematch_after_scheduled_refresh": bool(doc.get("rematch_after_scheduled_refresh", False)),
        "settings_updated_at": doc.get("settings_updated_at"),
    }


def save_tally_master_scheduler_settings(
    *,
    mode: str,
    frequency_minutes: int,
    rematch_after_scheduled_refresh: bool = False,
) -> dict[str, Any]:
    mode = (mode or "").strip().lower()
    if mode not in ("manual", "scheduled"):
        raise ValueError("mode must be 'manual' or 'scheduled'")

    try:
        frequency_minutes = int(frequency_minutes)
    except Exception as exc:
        raise ValueError("frequency_minutes must be a whole number") from exc
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
                "rematch_after_scheduled_refresh": bool(rematch_after_scheduled_refresh),
                "settings_updated_at": datetime.utcnow(),
            }
        },
        upsert=True,
    )
    logger.info(
        "[tally_master_settings] Saved scheduler: mode=%s frequency_minutes=%s rematch=%s",
        mode,
        frequency_minutes,
        rematch_after_scheduled_refresh,
    )
    return get_tally_master_scheduler_settings()


def next_refresh_baseline(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    """Timestamp the next refresh countdown is measured from."""
    from backend import tally_master_db

    s = settings or get_tally_master_scheduler_settings()
    meta = tally_master_db.get_sync_metadata()
    candidates = [
        t
        for t in (meta.get("last_synced_at"), s.get("settings_updated_at"))
        if t is not None
    ]
    return max(candidates) if candidates else None


def due_for_scheduled_refresh(settings: Optional[dict[str, Any]] = None) -> bool:
    """True when scheduled mode is on and the refresh interval has elapsed."""
    from backend import tally_master_db
    from backend.tally_integration.config import is_tally_configured

    if not is_tally_configured():
        return False

    s = settings or get_tally_master_scheduler_settings()
    if s["mode"] != "scheduled":
        return False

    meta = tally_master_db.get_sync_metadata()
    if meta.get("syncing"):
        return False

    baseline = next_refresh_baseline(s)
    if baseline is None:
        return True

    elapsed_minutes = (datetime.utcnow() - baseline).total_seconds() / 60.0
    return elapsed_minutes >= s["frequency_minutes"]
