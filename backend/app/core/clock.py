"""Time source for replay. Production uses UTC now. Replay passes a fixed instant.

Services that already accept `now` keep that parameter. This object is the one place a caller
gets the instant, so a replay does not call the wall clock itself.
"""
from __future__ import annotations

from datetime import datetime

from app.core.db import utcnow


class Clock:
    def now(self) -> datetime:
        raise NotImplementedError


class SystemClock(Clock):
    def now(self) -> datetime:
        return utcnow()


class FixedClock(Clock):
    def __init__(self, instant: datetime):
        if instant.tzinfo is None:
            raise ValueError("replay clock must be timezone-aware UTC")
        self._instant = instant

    def now(self) -> datetime:
        return self._instant
