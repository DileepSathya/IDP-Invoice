"""Persisted Tally master-data refresh scheduler settings.

Three modes, configured from Settings → Tally Master Data:
- "manual" (default): master data is refreshed only via the Refresh button.
- "scheduled": the background scheduler pulls vendors/items/POs from Tally every
  N minutes (interval-based).
- "time_based": the background scheduler pulls at specific times each day in the
  configured timezone.

The manual Refresh button remains available alongside any scheduler mode.

Settings live in MongoDB (erp_settings collection) so the API and background
scheduler thread always agree on the current mode/frequency/times.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from backend.agents.database import get_db

logger = logging.getLogger(__name__)

_SETTINGS_DOC_ID = "tally_master_scheduler"
DEFAULT_MODE = "manual"
DEFAULT_FREQUENCY_MINUTES = 60
MIN_FREQUENCY_MINUTES = 1
MAX_FREQUENCY_MINUTES = 1440  # 24h
DEFAULT_TIMEZONE = "UTC"
MAX_SCHEDULED_TIMES = 48
_TIME_PATTERN = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")


def _collection():
    return get_db()["erp_settings"]


def _parse_time_hhmm(value: str) -> tuple[int, int]:
    match = _TIME_PATTERN.match((value or "").strip())
    if not match:
        raise ValueError(f"Invalid time '{value}'; use 24-hour HH:mm format (e.g. 08:00, 13:30).")
    return int(match.group(1)), int(match.group(2))


def _normalize_scheduled_times(times: list[str]) -> list[str]:
    if not times:
        return []

    normalized: list[str] = []
    seen: set[str] = set()
    for raw in times:
        hour, minute = _parse_time_hhmm(raw)
        canonical = f"{hour:02d}:{minute:02d}"
        if canonical in seen:
            raise ValueError(f"Duplicate scheduled time: {canonical}")
        seen.add(canonical)
        normalized.append(canonical)

    normalized.sort(key=lambda t: (_parse_time_hhmm(t)[0], _parse_time_hhmm(t)[1]))
    if len(normalized) > MAX_SCHEDULED_TIMES:
        raise ValueError(f"At most {MAX_SCHEDULED_TIMES} scheduled times are allowed.")
    return normalized


def _validate_timezone(tz_name: str) -> str:
    name = (tz_name or "").strip()
    if not name:
        raise ValueError("timezone is required for time-based scheduling.")
    try:
        ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"Invalid timezone: {name}") from exc
    return name


def get_tally_master_scheduler_settings() -> dict[str, Any]:
    try:
        doc = _collection().find_one({"_id": _SETTINGS_DOC_ID}) or {}
    except Exception as exc:
        logger.warning("[tally_master_settings] Could not read settings, using defaults: %s", exc)
        doc = {}

    mode = doc.get("mode") or DEFAULT_MODE
    if mode not in ("manual", "scheduled", "time_based"):
        mode = DEFAULT_MODE

    frequency = doc.get("frequency_minutes") or DEFAULT_FREQUENCY_MINUTES
    try:
        frequency = int(frequency)
    except Exception:
        frequency = DEFAULT_FREQUENCY_MINUTES
    frequency = max(MIN_FREQUENCY_MINUTES, min(MAX_FREQUENCY_MINUTES, frequency))

    scheduled_times_raw = doc.get("scheduled_times") or []
    scheduled_times: list[str] = []
    if isinstance(scheduled_times_raw, list):
        try:
            scheduled_times = _normalize_scheduled_times([str(t) for t in scheduled_times_raw])
        except ValueError:
            scheduled_times = []

    timezone_name = doc.get("timezone") or DEFAULT_TIMEZONE
    try:
        timezone_name = _validate_timezone(timezone_name)
    except ValueError:
        timezone_name = DEFAULT_TIMEZONE

    slot_last_run_raw = doc.get("slot_last_run") or {}
    slot_last_run: dict[str, datetime] = {}
    if isinstance(slot_last_run_raw, dict):
        for slot, ts in slot_last_run_raw.items():
            if isinstance(ts, datetime):
                slot_last_run[str(slot)] = ts

    return {
        "mode": mode,
        "frequency_minutes": frequency,
        "scheduled_times": scheduled_times,
        "timezone": timezone_name,
        "slot_last_run": slot_last_run,
        "rematch_after_scheduled_refresh": bool(doc.get("rematch_after_scheduled_refresh", False)),
        "settings_updated_at": doc.get("settings_updated_at"),
    }


def save_tally_master_scheduler_settings(
    *,
    mode: str,
    frequency_minutes: int,
    rematch_after_scheduled_refresh: bool = False,
    scheduled_times: Optional[list[str]] = None,
    timezone_name: Optional[str] = None,
) -> dict[str, Any]:
    mode = (mode or "").strip().lower()
    if mode not in ("manual", "scheduled", "time_based"):
        raise ValueError("mode must be 'manual', 'scheduled', or 'time_based'")

    try:
        frequency_minutes = int(frequency_minutes)
    except Exception as exc:
        raise ValueError("frequency_minutes must be a whole number") from exc
    if frequency_minutes < MIN_FREQUENCY_MINUTES or frequency_minutes > MAX_FREQUENCY_MINUTES:
        raise ValueError(
            f"frequency_minutes must be between {MIN_FREQUENCY_MINUTES} and {MAX_FREQUENCY_MINUTES}"
        )

    normalized_times: list[str] = []
    tz = DEFAULT_TIMEZONE
    if mode == "time_based":
        if scheduled_times is None:
            existing = get_tally_master_scheduler_settings()
            normalized_times = existing["scheduled_times"]
        else:
            normalized_times = _normalize_scheduled_times(scheduled_times)
        if not normalized_times:
            raise ValueError("Add at least one scheduled time for time-based scheduling.")
        if timezone_name is None:
            existing = get_tally_master_scheduler_settings()
            tz = existing["timezone"]
        else:
            tz = _validate_timezone(timezone_name)
    elif scheduled_times is not None:
        normalized_times = _normalize_scheduled_times(scheduled_times)
        if timezone_name is not None:
            tz = _validate_timezone(timezone_name)

    update_fields: dict[str, Any] = {
        "mode": mode,
        "frequency_minutes": frequency_minutes,
        "rematch_after_scheduled_refresh": bool(rematch_after_scheduled_refresh),
        "settings_updated_at": datetime.utcnow(),
    }
    if mode == "time_based" or scheduled_times is not None:
        update_fields["scheduled_times"] = normalized_times
    if mode == "time_based" or timezone_name is not None:
        update_fields["timezone"] = tz

    _collection().update_one(
        {"_id": _SETTINGS_DOC_ID},
        {"$set": update_fields},
        upsert=True,
    )
    logger.info(
        "[tally_master_settings] Saved scheduler: mode=%s frequency_minutes=%s "
        "times=%s timezone=%s rematch=%s",
        mode,
        frequency_minutes,
        normalized_times if mode == "time_based" else "-",
        tz if mode == "time_based" else "-",
        rematch_after_scheduled_refresh,
    )
    return get_tally_master_scheduler_settings()


def mark_time_slots_run(slots: list[str]) -> None:
    """Record that the given daily time slots were executed (UTC timestamps)."""
    if not slots:
        return
    now = datetime.utcnow()
    update = {f"slot_last_run.{slot}": now for slot in slots}
    _collection().update_one({"_id": _SETTINGS_DOC_ID}, {"$set": update}, upsert=True)


def _local_now(settings: dict[str, Any]) -> datetime:
    tz = ZoneInfo(settings["timezone"])
    return datetime.now(tz)


def _occurrence_today(local_now: datetime, hour: int, minute: int) -> datetime:
    return local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)


def get_due_time_slots(settings: Optional[dict[str, Any]] = None) -> list[str]:
    """Return scheduled HH:mm slots whose daily occurrence has passed but not yet run."""
    s = settings or get_tally_master_scheduler_settings()
    if s["mode"] != "time_based" or not s.get("scheduled_times"):
        return []

    local_now = _local_now(s)
    slot_last_run: dict[str, datetime] = s.get("slot_last_run") or {}
    due: list[str] = []

    for time_str in s["scheduled_times"]:
        hour, minute = _parse_time_hhmm(time_str)
        occurrence = _occurrence_today(local_now, hour, minute)
        if local_now < occurrence:
            continue

        last_run = slot_last_run.get(time_str)
        if last_run is None:
            due.append(time_str)
            continue

        if last_run.tzinfo is None:
            last_run = last_run.replace(tzinfo=timezone.utc)
        last_run_local = last_run.astimezone(local_now.tzinfo)
        if last_run_local < occurrence:
            due.append(time_str)

    return due


def next_time_based_refresh_at(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    """Next upcoming scheduled time (UTC), or None if none configured."""
    s = settings or get_tally_master_scheduler_settings()
    times = s.get("scheduled_times") or []
    if not times:
        return None

    local_now = _local_now(s)
    candidates: list[datetime] = []

    for time_str in times:
        hour, minute = _parse_time_hhmm(time_str)
        occurrence = _local_now(s).replace(hour=hour, minute=minute, second=0, microsecond=0)
        if occurrence <= local_now:
            occurrence += timedelta(days=1)
        candidates.append(occurrence)

    if not candidates:
        return None
    next_local = min(candidates)
    return next_local.astimezone(timezone.utc).replace(tzinfo=None)


def next_refresh_baseline(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    """Timestamp the next refresh countdown is measured from (interval mode)."""
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
    """True when a scheduled refresh should run (interval or time-based)."""
    from backend import tally_master_db
    from backend.tally_integration.config import is_tally_configured

    if not is_tally_configured():
        return False

    s = settings or get_tally_master_scheduler_settings()
    if s["mode"] == "manual":
        return False

    meta = tally_master_db.get_sync_metadata()
    if meta.get("syncing"):
        return False

    if s["mode"] == "time_based":
        return len(get_due_time_slots(s)) > 0

    baseline = next_refresh_baseline(s)
    if baseline is None:
        return True

    elapsed_minutes = (datetime.utcnow() - baseline).total_seconds() / 60.0
    return elapsed_minutes >= s["frequency_minutes"]


def compute_next_refresh_at(settings: Optional[dict[str, Any]] = None) -> Optional[datetime]:
    s = settings or get_tally_master_scheduler_settings()
    if s["mode"] == "scheduled":
        baseline = next_refresh_baseline(s)
        if baseline is None:
            return None
        return baseline + timedelta(minutes=s["frequency_minutes"])
    if s["mode"] == "time_based":
        due = get_due_time_slots(s)
        if due:
            return datetime.utcnow()
        return next_time_based_refresh_at(s)
    return None
