"""Profound buckets everything in US Eastern Time; v2 report dates are inclusive YYYY-MM-DD (ET)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")


def et_today(now: datetime | None = None) -> date:
    return (now or datetime.now(UTC)).astimezone(ET).date()


def et_midnight_utc(d: date | str) -> datetime:
    """Start of the ET day as an aware UTC datetime (used as Signal.observed_at for daily buckets)."""
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return datetime(d.year, d.month, d.day, tzinfo=ET).astimezone(UTC)


def last_complete_day(now: datetime | None = None) -> date:
    """Yesterday (ET): today's bucket is partial and would look like a false drop."""
    return et_today(now) - timedelta(days=1)
