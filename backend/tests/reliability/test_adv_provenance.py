"""Fixture/replay data must never be labelled live, and the future must never leak into a decision."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.domain.source_mode import SignalSourceMode as M
from app.domain.source_mode import source_mode_for_signal as mode
from app.incidents.detector import detect_incidents, historical_replay
from app.models.core import Incident
from sqlalchemy import select

from tests import factories as f


@pytest.mark.parametrize(("source", "raw", "expected"), [
    ("profound", {}, M.LIVE),
    ("profound", {"source_mode": "LIVE"}, M.LIVE),
    ("dev_fixture", {}, M.FIXTURE),
    ("dev_fixture", {"source_mode": "LIVE"}, M.FIXTURE),        # persisted claim cannot launder a fixture label
    ("profound", {"source_mode": "FIXTURE"}, M.FIXTURE),
    ("profound", {"source_mode": "REPLAY"}, M.REPLAY),
    ("profound", {"_fixture": True, "source_mode": "LIVE"}, M.FIXTURE),
    ("historical_replay", {"source_mode": "LIVE"}, M.REPLAY),
    ("profound", {"source_mode": "nonsense"}, M.LIVE),         # unknown tag is ignored; label decides
    ("test_factory", {}, M.TEST),
    ("", {}, M.LIVE),
])
def test_signal_is_live_only_when_everything_says_live(source, raw, expected):
    assert mode(source, raw) is expected


async def test_fixture_signals_yield_an_incident_that_is_not_labelled_live(session, org):
    await f.make_signal_series(session, org, metric="visibility", values=f.visibility_drop_values(), kind="visibility")
    from app.models.core import Signal

    for s in (await session.execute(select(Signal))).scalars():
        s.source = "dev_fixture"
    await session.commit()
    (inc,) = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    inc = await session.get(Incident, inc.id)
    assert inc.context["provenance"] != "live", inc.context["provenance"]
    assert inc.context["provenance"] == "test_fixture"


async def test_mixed_sources_are_mixed_never_live(session, org):
    from app.incidents.detector import _provenance

    assert _provenance(["profound", "dev_fixture"]) == "mixed"
    assert _provenance(["profound"]) == "live"
    assert _provenance([]) == "unknown"


def test_replay_never_sees_the_future_even_with_one_future_point_that_would_trigger():
    from datetime import UTC, datetime

    from app.incidents.detector import SignalPoint

    t0 = datetime(2026, 9, 1, tzinfo=UTC)
    pts = [SignalPoint(metric="visibility", value=v, observed_at=t0 + timedelta(days=i), prompt_cluster_id="c",
                       raw={}, source="dev_fixture", signal_id=f"s{i}") for i, v in enumerate([62] * 10 + [30])]
    assert historical_replay(pts, as_of=t0 + timedelta(days=9, hours=23))["would_detect"] == 0
    assert historical_replay(pts, as_of=t0 + timedelta(days=11))["would_detect"] >= 1
