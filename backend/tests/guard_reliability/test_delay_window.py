"""DELAY eligible_after is the window service's value, never invented."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from tests.guard_reliability.support import (
    changeset,
    decision,
    eligible_after,
    expected_after,
    iso_close,
    post,
    protected_experiment,
)


async def test_eligible_after_equals_window_start_when_measurement_not_started(client, session, org):
    _, _, exp = await protected_experiment(session, org, status="executed")
    r = await post(client, changeset(org))
    assert decision(r) == "DELAY"
    assert iso_close(eligible_after(r), expected_after(exp))


async def test_eligible_after_follows_window_settings(client, session, org):
    ex = datetime.now(UTC) - timedelta(hours=3)
    _, _, exp = await protected_experiment(session, org, executed_at=ex, delay_hours=24)
    r = await post(client, changeset(org))
    assert iso_close(eligible_after(r), expected_after(exp))
    assert datetime.fromisoformat(eligible_after(r).replace("Z", "+00:00")) > datetime.now(UTC)


async def test_two_overlapping_experiments_use_latest_eligible_time(client, session, org):
    now = datetime.now(UTC)
    _, _, e1 = await protected_experiment(session, org, executed_at=now - timedelta(hours=1))
    _, _, e2 = await protected_experiment(session, org, executed_at=now)
    r = await post(client, changeset(org))
    latest = max(expected_after(e1), expected_after(e2))
    assert decision(r) == "DELAY"
    assert iso_close(eligible_after(r), latest)


async def test_retry_after_window_does_not_auto_approve(client, session, org):
    """A DELAY never turns into approval by itself: after the window passes a retry is a fresh decision."""
    past = datetime.now(UTC) - timedelta(days=30)
    await protected_experiment(session, org, executed_at=past, status="awaiting_verification")
    r = await post(client, changeset(org, idempotency_key="after-window"))
    # measurement still not complete (status is awaiting_verification): must still be DELAY, never ALLOW
    # overdue verification has no completion time: REQUIRE_REVIEW (explicit reason), never ALLOW, never a time-less DELAY
    assert decision(r) == "REQUIRE_REVIEW", r.json()
    assert "overdue" in r.text
