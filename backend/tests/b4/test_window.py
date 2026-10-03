"""Verification-window domain service: boundary semantics, injected clock, source timestamps (B4 task 8)."""
from datetime import UTC, datetime, timedelta

import pytest
from app.core.clock import FixedClock
from app.experiments import window as w

EXEC = datetime(2026, 10, 2, 20, 29, 43, tzinfo=UTC)  # same instant as the fixture experiment's execution
WIN = w.VerificationWindow(delay=timedelta(hours=48), duration=timedelta(days=7))
START, END = WIN.bounds(EXEC)
SEC = timedelta(seconds=1)


def test_bounds_match_fixture_eligibility_time():
    assert START == datetime(2026, 10, 4, 20, 29, 43, tzinfo=UTC)
    assert END == START + timedelta(days=7)


@pytest.mark.parametrize("delta,ok,code", [(-SEC, False, "window_not_open"), (timedelta(0), True, "eligible"),
                                           (SEC, True, "eligible")])
def test_attempt_boundary_start_inclusive(delta, ok, code):
    e = w.attempt_allowed(START, START + delta)
    assert e.ok is ok and e.code == code


@pytest.mark.parametrize("delta,ok,code", [(-SEC, False, "before_window"), (timedelta(0), True, "eligible"),
                                           (SEC, True, "eligible")])
def test_measurement_boundary_uses_source_timestamp(delta, ok, code):
    now = START + timedelta(days=1)  # request time is well inside the window; only observed_at decides
    e = w.measurement_eligible(window_start=START, window_end=END, executed_at=EXEC, observed_at=START + delta, at=now)
    assert e.ok is ok and e.code == code


def test_request_time_never_makes_an_old_measurement_eligible():
    old = START - SEC
    late_request = END + timedelta(days=30)
    e = w.measurement_eligible(window_start=START, window_end=END, executed_at=EXEC, observed_at=old, at=late_request)
    assert not e.ok and e.code == "before_window"


def test_future_measurement_rejected_and_late_measurement_flagged_but_eligible():
    now = START + timedelta(days=2)
    fut = w.measurement_eligible(window_start=START, window_end=END, executed_at=EXEC, observed_at=now + SEC, at=now)
    assert not fut.ok and fut.code == "future_measurement"
    late_now = END + timedelta(days=3)
    late = w.measurement_eligible(window_start=START, window_end=END, executed_at=EXEC,
                                  observed_at=END + SEC, at=late_now)
    assert late.ok and late.late


def test_dry_run_and_missing_window_never_eligible():
    assert w.attempt_allowed(START, START + timedelta(days=1), dry_run=True).code == "dry_run"
    assert w.attempt_allowed(None, START).code == "no_window"


def test_naive_datetimes_are_read_as_utc():
    naive = START.replace(tzinfo=None)
    assert w.attempt_allowed(naive, START).ok


def test_injected_clock_is_the_single_time_source():
    with w.use_clock(FixedClock(START - SEC)):
        assert not w.attempt_allowed(START).ok
        assert w.now() == START - SEC
    with w.use_clock(FixedClock(START)):
        assert w.attempt_allowed(START).ok
    assert abs((w.now() - datetime.now(UTC)).total_seconds()) < 5  # production default: system UTC
