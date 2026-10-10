"""Timestamp and calendar helpers.

Storage convention: every timestamp is persisted as a *naive* datetime that
represents UTC, serialised with second precision ("YYYY-MM-DDTHH:MM:SS").
Because the format is fixed-width, lexicographic comparison in SQL matches
chronological order.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from functools import lru_cache
from typing import Iterator, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .clock import Clock

UTC = timezone.utc
STORAGE_FORMAT_TIMESPEC = "seconds"


@lru_cache(maxsize=128)
def get_zone(name: str) -> ZoneInfo:
    """Return the ZoneInfo for ``name`` or raise ValueError."""
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError(f"unknown timezone: {name!r}") from exc


def to_utc_naive(value: datetime) -> datetime:
    """Normalise ``value`` to a naive UTC datetime (the storage form)."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


def from_utc_naive(value: datetime) -> datetime:
    """Attach UTC to a naive storage datetime."""
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    return value.replace(tzinfo=UTC)


def to_storage(value: datetime) -> str:
    return to_utc_naive(value).replace(microsecond=0).isoformat(
        timespec=STORAGE_FORMAT_TIMESPEC
    )


def from_storage(text: str) -> datetime:
    return datetime.fromisoformat(text)


def to_local(value: datetime, tz_name: str) -> datetime:
    """Convert a stored (naive UTC) or aware datetime to the zone ``tz_name``."""
    return from_utc_naive(value).astimezone(get_zone(tz_name))


def format_local(value: datetime, tz_name: str, fmt: str = "%Y-%m-%d %H:%M") -> str:
    return to_local(value, tz_name).strftime(fmt)


def day_bounds(day: date) -> Tuple[datetime, datetime]:
    """Return the half-open ``[start, end)`` range covering ``day``.

    Bounds are naive datetimes so they can be compared directly with stored
    timestamps (which are naive UTC).
    """
    start = datetime.combine(day, time.min)
    end = start + timedelta(days=1)
    return start, end


def today(clock: Clock) -> date:
    """The current calendar day according to ``clock``."""
    return clock.now().date()


def iter_days(first: date, last: date) -> Iterator[date]:
    """Yield each day from ``first`` to ``last`` inclusive."""
    if last < first:
        raise ValueError("last day is before first day")
    current = first
    while current <= last:
        yield current
        current += timedelta(days=1)


def parse_day(text: str) -> date:
    return date.fromisoformat(text.strip())


def week_start(day: date) -> date:
    """Monday of the ISO week containing ``day``."""
    return day - timedelta(days=day.weekday())
