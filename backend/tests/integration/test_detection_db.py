"""Signals -> incidents against the real DB (detector + priority, no LLM)."""
from __future__ import annotations

from app.domain.enums import IncidentCategory, IncidentState, Severity
from app.incidents.detector import detect_incidents
from app.models.core import Incident
from sqlalchemy import select

from tests import factories as f


async def _drop(session, org, cluster=None, **kw):
    await f.make_signal_series(session, org, metric="visibility",
                               values=f.visibility_drop_values(**kw), cluster=cluster)


async def test_visibility_drop_creates_incident(session, org):
    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    await _drop(session, org, cluster)
    created = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    assert len(created) == 1
    inc = created[0]
    assert inc.category == IncidentCategory.VISIBILITY_DROP.value
    assert inc.state == IncidentState.DETECTED.value
    assert inc.number >= 1
    assert inc.prompt_cluster_id == cluster.id
    assert inc.severity in {s.value for s in Severity}
    assert 0 <= inc.priority <= 100
    assert inc.priority_breakdown, "priority must expose its components"
    assert inc.metrics and inc.metrics[0]["key"] == "visibility"
    assert "Enterprise SSO" in inc.title


async def test_detected_metrics_reflect_real_signal_values(session, org):
    await _drop(session, org, before=60.0, after=35.0)
    (inc,) = await detect_incidents(session, org.id, now=f.NOW)
    blob = str(inc.metrics)
    assert "35" in blob and ("60" in blob or "6" in blob), blob


async def test_stable_series_creates_no_incident(session, org):
    await f.make_signal_series(session, org, metric="visibility", values=[60.0, 61.0, 59.0, 60.0, 60.5, 60.2, 59.8])
    assert await detect_incidents(session, org.id, now=f.NOW) == []


async def test_no_signals_means_no_incidents(session, org):
    assert await detect_incidents(session, org.id, now=f.NOW) == []
    rows = (await session.execute(select(Incident))).scalars().all()
    assert rows == []


async def test_duplicate_detection_is_idempotent(session, org):
    await _drop(session, org)
    first = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    second = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    assert len(first) == 1 and second == []
    assert len((await session.execute(select(Incident))).scalars().all()) == 1


async def test_detection_is_scoped_to_org(session, org):
    other = await f.make_org(session)
    await _drop(session, other)
    assert await detect_incidents(session, org.id, now=f.NOW) == []


async def test_small_noise_below_threshold_ignored(session, org):
    await _drop(session, org, before=60.0, after=58.5)
    assert await detect_incidents(session, org.id, now=f.NOW) == []


async def test_later_equivalent_signal_does_not_create_second_incident(session, org):
    """V2: a new observation of the same sustained drop (later `now`, extra signal) must not open a duplicate."""
    from datetime import timedelta

    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    await _drop(session, org, cluster)
    first = await detect_incidents(session, org.id, now=f.NOW)
    await session.commit()
    await f.make_signal_series(session, org, metric="visibility", values=[36.5], end=f.NOW + timedelta(days=1),
                               cluster=cluster)
    second = await detect_incidents(session, org.id, now=f.NOW + timedelta(days=1))
    await session.commit()
    assert len(first) == 1 and second == []
    assert len((await session.execute(select(Incident))).scalars().all()) == 1


async def test_pipeline_detect_twice_creates_one_incident(session, org, no_queue):
    from app.services import pipeline

    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    await _drop(session, org, cluster)
    a = await pipeline.detect(session, org.id)
    b = await pipeline.detect(session, org.id)
    assert len(a["created"]) == 1 and b["created"] == []
    assert len((await session.execute(select(Incident))).scalars().all()) == 1


async def test_detection_is_deterministic_and_has_transparent_priority(session, org):
    from app.incidents.priority import COMPONENTS

    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    await _drop(session, org, cluster)
    (inc,) = await detect_incidents(session, org.id, now=f.NOW)
    comps = inc.priority_breakdown["components"]
    assert set(comps) == set(COMPONENTS)
    assert all({"value", "weight", "source", "label"} <= set(c) for c in comps.values())
    assert abs(sum(c["weight"] for c in comps.values()) - 1.0) < 1e-6
    # no revenue/dollar claims anywhere in the stored incident text or priority breakdown
    blob = (inc.title + inc.summary + str(inc.priority_breakdown) + str(inc.context)).lower()
    assert "revenue" not in blob and "$" not in blob
