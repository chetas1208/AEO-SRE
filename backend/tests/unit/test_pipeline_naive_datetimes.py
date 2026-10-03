"""Regression: SQLite returns tz-naive datetimes. The gate stage's feature extraction used to raise
`TypeError: can't subtract offset-naive and offset-aware datetimes`, which aborted the gate step and silently
left every hypothesis unconfirmed (SQLite only; Postgres returns aware values)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services.pipeline import _features_from


def _ev(observed_at):
    return SimpleNamespace(type="owned", status="live", excerpt="text", observed_at=observed_at,
                           contradiction_score=None)


def _inc():
    return SimpleNamespace(category="displacement")


def test_features_from_handles_naive_and_aware_observed_at():
    base = datetime.now(UTC) - timedelta(days=3, hours=1)
    naive = _features_from(_inc(), [_ev(base.replace(tzinfo=None))], None, {})
    aware = _features_from(_inc(), [_ev(base)], None, {})
    assert naive["source_age_days"] == aware["source_age_days"] == 3.0
