"""Injectable clocks.

Everything that needs "now" takes a clock so tests (and backfills) can pin
time. Clocks always return timezone-aware datetimes in UTC.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:  # pragma: no cover - protocol
        ...


class SystemClock:
    """Wall-clock time."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FixedClock:
    """A clock frozen at a given instant.

    Naive datetimes are interpreted as UTC. Aware datetimes in any zone are
    accepted and normalised to UTC.
    """

    def __init__(self, instant: datetime) -> None:
        if instant.tzinfo is None:
            instant = instant.replace(tzinfo=timezone.utc)
        self._instant = instant.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._instant

    def advance(self, **kwargs: float) -> None:
        self._instant = self._instant + timedelta(**kwargs)

    def __repr__(self) -> str:
        return f"FixedClock({self._instant.isoformat()})"


def parse_instant(text: str) -> datetime:
    """Parse an ISO-8601 instant for ``--now`` style options.

    Accepts a trailing ``Z``. Naive input is taken as UTC.
    """
    value = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
