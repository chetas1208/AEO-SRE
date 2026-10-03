"""The ONE verification-window domain service. API, worker, CLI and frontend ask this module; none re-implements it.

Semantics (all instants are timezone-aware UTC; a naive datetime is read as UTC):

    window_start = executed_at + delay        delay = settings.verification_delay_hours (Profound reports lag 24-48h)
    window_end   = window_start + duration

    * A verification ATTEMPT is allowed iff now >= window_start          (start is INCLUSIVE)
          start - 1s -> rejected, start -> allowed, start + 1s -> allowed
    * A measurement is ELIGIBLE iff  observed_at >= window_start          (inclusive)
                                 and observed_at >  executed_at
                                 and observed_at <= now                   (a measurement cannot come from the future)
      `observed_at` is the timestamp the SOURCE attached to the measurement (Profound `observed_at`), never the time
      of the request. A measurement later than `window_end` is still eligible (a stalled worker must not strand an
      experiment forever) but is reported as `late` and the outcome is labelled accordingly.
    * `now` always comes from the injected Clock: SystemClock (UTC) in production, FixedClock in tests.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.core.clock import Clock, SystemClock

_clock: ContextVar[Clock | None] = ContextVar("verification_clock", default=None)


def get_clock() -> Clock:
    return _clock.get() or SystemClock()


@contextlib.contextmanager
def use_clock(clock: Clock) -> Iterator[Clock]:
    """Inject a clock (tests, replay). Restores the previous one on exit."""
    token = _clock.set(clock)
    try:
        yield clock
    finally:
        _clock.reset(token)


def now() -> datetime:
    return aware(get_clock().now())


def aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@dataclass(frozen=True)
class VerificationWindow:
    """Profound data lags 24-48h: measurements before `delay` after execution are not counted."""

    delay: timedelta = timedelta(hours=24)
    duration: timedelta = timedelta(days=7)

    def bounds(self, anchor: datetime) -> tuple[datetime, datetime]:
        start = aware(anchor) + self.delay
        return start, start + self.duration


DEFAULT_WINDOW = VerificationWindow()


def window_from_settings() -> VerificationWindow:
    from app.core.config import get_settings

    return VerificationWindow(delay=timedelta(hours=float(get_settings().verification_delay_hours)))


@dataclass(frozen=True)
class Eligibility:
    ok: bool
    code: str  # eligible | no_window | dry_run | not_executed | window_not_open | before_window | future_measurement
    reason: str
    late: bool = False  # measurement is after window_end (still eligible)
    opens_at: datetime | None = None

    def __bool__(self) -> bool:
        return self.ok


def attempt_allowed(window_start: datetime | None, at: datetime | None = None, *, dry_run: bool = False) -> Eligibility:
    """May verification be attempted at `at` (default: the injected clock)? Start inclusive."""
    t = aware(at) if at is not None else now()
    if dry_run:
        return Eligibility(False, "dry_run", "dry-run execution changed nothing; it is never verified")
    if window_start is None:
        return Eligibility(False, "no_window", "experiment has no verification window (not executed)")
    start = aware(window_start)
    if t < start:
        return Eligibility(False, "window_not_open", f"verification window opens {start.isoformat()}", opens_at=start)
    return Eligibility(True, "eligible", "verification window is open", opens_at=start)


def measurement_eligible(
    *, window_start: datetime | None, window_end: datetime | None, executed_at: datetime | None,
    observed_at: datetime, at: datetime | None = None, dry_run: bool = False,
) -> Eligibility:
    """May a measurement taken at `observed_at` (source timestamp) count as the post-intervention outcome?"""
    t = aware(at) if at is not None else now()
    gate = attempt_allowed(window_start, t, dry_run=dry_run)
    if not gate.ok:
        return gate
    if executed_at is None:
        return Eligibility(False, "not_executed", "experiment has no execution time")
    obs, start = aware(observed_at), aware(window_start)  # type: ignore[arg-type]
    if obs < start or obs <= aware(executed_at):
        return Eligibility(False, "before_window",
                           f"measurement {obs.isoformat()} predates the verification window ({start.isoformat()})",
                           opens_at=start)
    if obs > t:
        return Eligibility(False, "future_measurement", f"measurement {obs.isoformat()} is after the current time")
    late = window_end is not None and obs > aware(window_end)
    return Eligibility(True, "eligible", "measurement is inside/after the verification window", late=late,
                       opens_at=start)


def experiment_eligibility(experiment, observed_at: datetime | None = None, at: datetime | None = None) -> Eligibility:
    """Convenience wrapper over an Experiment row: attempt check, plus the measurement check when `observed_at` given."""
    if observed_at is None:
        return attempt_allowed(experiment.verification_window_start, at, dry_run=bool(experiment.dry_run))
    return measurement_eligible(
        window_start=experiment.verification_window_start, window_end=experiment.verification_window_end,
        executed_at=experiment.executed_at, observed_at=observed_at, at=at, dry_run=bool(experiment.dry_run))
