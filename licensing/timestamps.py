"""UTC timestamp helpers for license issuedAt and quota period boundaries."""

from __future__ import annotations

from datetime import date, datetime, time, timezone


def utc_now_iso() -> str:
    """Current UTC issue time for new licenses (seconds precision, Z suffix)."""
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .strftime("%Y-%m-%dT%H:%M:%SZ")
    )


def parse_license_timestamp(value: str) -> datetime:
    """
    Parse license issuedAt for MongoDB quota counting.

    - Date only (YYYY-MM-DD): UTC midnight (legacy licenses).
    - Datetime (YYYY-MM-DDTHH:MM:SS or with Z / offset): exact UTC instant.
    """
    raw = str(value).strip()
    if not raw:
        raise ValueError("issuedAt is empty")

    normalized = raw.replace(" ", "T")
    if normalized.endswith("Z"):
        normalized = f"{normalized[:-1]}+00:00"

    if "T" in normalized:
        dt = datetime.fromisoformat(normalized)
        if dt.tzinfo is not None:
            return dt.astimezone(timezone.utc).replace(tzinfo=None)
        return dt

    return datetime.combine(date.fromisoformat(normalized), time.min)
