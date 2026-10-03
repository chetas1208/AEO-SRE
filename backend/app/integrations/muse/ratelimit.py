"""In-memory sliding-window rate limiter, per bucket (key id). One API process; put a proxy limiter in front to scale."""
from __future__ import annotations

import threading
import time
from collections import deque

from app.integrations.muse import errors as E

WINDOW_S = 60.0


class SlidingWindow:
    def __init__(self, window_s: float = WINDOW_S) -> None:
        self.window_s = window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def hit(self, bucket: str, limit: int, *, now: float | None = None) -> None:
        if limit <= 0:
            return
        t = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(bucket, deque())
            while q and t - q[0] > self.window_s:
                q.popleft()
            if len(q) >= limit:
                wait = int(self.window_s - (t - q[0])) + 1
                raise E.RateLimited("rate limit exceeded; slow down",
                                    {"limit_per_minute": limit, "retry_after_seconds": wait},
                                    headers={"Retry-After": str(wait)})
            q.append(t)

    def clear(self) -> None:
        with self._lock:
            self._hits.clear()


LIMITER = SlidingWindow()
