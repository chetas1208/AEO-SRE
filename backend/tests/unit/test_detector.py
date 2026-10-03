"""Unit tests for the incident detector.

All series below are synthetic TEST FIXTURES built inside this file to exercise the algorithm. They are not
Profound data and are never used by app code.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import IncidentCategory, IncidentState, Severity
from app.incidents.detector import (
    DetectionConfig,
    DetectionContext,
    ExistingIncident,
    SignalPoint,
    detect_from_series,
    detect_incidents,
    filter_duplicates,
    historical_replay,
)

T0 = datetime(2026, 9, 1, tzinfo=UTC)
NOW = T0 + timedelta(days=20)
CL = "11111111-1111-1111-1111-111111111111"
CL2 = "22222222-2222-2222-2222-222222222222"


def series(metric, values, *, cluster=CL, start=T0, step_days=1, raw=None, baseline=None, source="profound"):
    return [
        SignalPoint(
            metric=metric, value=v, observed_at=start + timedelta(days=i * step_days), prompt_cluster_id=cluster,
            raw=raw or {}, baseline=baseline, source=source, signal_id=f"{metric}-{i}",
        )
        for i, v in enumerate(values)
    ]


STABLE = [62, 63, 61, 62, 64, 62, 61, 63, 62, 62]


def detect(points, **kw):
    kw.setdefault("now", NOW)
    return detect_from_series(points, **kw)


def test_visibility_drop_detected_with_metric_delta_and_first_observed():
    pts = series("visibility", STABLE + [37])
    [d] = detect(pts)
    assert d.category is IncidentCategory.VISIBILITY_DROP
    m = d.metrics[0]
    assert (m.label, m.unit) == ("Visibility", "pp")
    assert m.before == pytest.approx(62) and m.after == 37 and m.delta == pytest.approx(-25)
    assert d.first_observed_at == T0 + timedelta(days=10)
    assert d.dedup_key == f"displacement:{CL}"
    assert "37" in d.title and "62" in d.title


def test_fraction_scale_is_normalised_to_percentage_points():
    pts = series("visibility", [v / 100 for v in STABLE] + [0.37])
    [d] = detect(pts)
    assert d.metrics[0].before == pytest.approx(62) and d.metrics[0].delta == pytest.approx(-25)


def test_normal_noise_does_not_fire():
    assert detect(series("visibility", STABLE + [60])) == []
    assert detect(series("visibility", [62, 70, 55, 66, 58, 69, 57, 64, 60, 61, 56])) == []  # noisy but no regime change


def test_improvement_is_not_an_incident():
    assert detect(series("visibility", STABLE + [85])) == []


def test_single_bump_below_absolute_minimum_ignored():
    assert detect(series("visibility", STABLE + [58])) == []  # -4pp < 5pp


def test_gradual_decline_first_observed_is_start_of_run():
    pts = series("visibility", STABLE + [55, 50, 44, 40])
    [d] = detect(pts)
    assert d.first_observed_at == T0 + timedelta(days=10)  # first point already >= half the threshold below baseline
    assert d.metrics[0].after == 40


def test_provided_baseline_used_only_when_history_is_short():
    pts = series("visibility", [40, 38], baseline=62)
    [d] = detect(pts)
    assert d.context["detection"][0]["baseline_source"] == "provided"
    assert d.confidence < 0.5  # no z-score, lower confidence than a rolling baseline
    # without any baseline and short history: nothing is invented
    assert detect(series("visibility", [40, 38])) == []
    # with enough history the rolling baseline wins over the provided one
    pts = series("visibility", STABLE + [37], baseline=30)
    [d2] = detect(pts)
    assert d2.metrics[0].before == pytest.approx(62)


def test_competitor_gain_merges_with_visibility_drop_in_one_incident():
    pts = (
        series("visibility", STABLE + [37])
        + series("competitor_share", [21, 22, 20, 21, 23, 21, 22, 21, 20, 21, 54], raw={"competitor": "Acme"})
    )
    [d] = detect(pts, context=DetectionContext(cluster_topics={CL: "Enterprise SSO"}))
    assert d.category is IncidentCategory.VISIBILITY_DROP
    labels = {m.label for m in d.metrics}
    assert {"Visibility", "Competitor share (Acme)"} <= labels
    assert d.topic == "Enterprise SSO" and d.title.startswith("Enterprise SSO:")
    assert d.priority.breakdown["competitive_displacement"].value == pytest.approx(1.0)


def test_competitor_gain_alone_names_competitor():
    pts = series("competitor_share", [21, 22, 20, 21, 23, 21, 22, 21, 20, 21, 40], raw={"competitor": "Acme"})
    [d] = detect(pts)
    assert d.category is IncidentCategory.COMPETITOR_CITATION_GAIN
    assert "Acme" in d.title


def test_each_competitor_is_its_own_series():
    pts = series("competitor_share", [20] * 9 + [45], raw={"competitor": "A"}) + series(
        "competitor_share", [30] * 10, raw={"competitor": "B"}
    )
    [d] = detect(pts)
    assert [m.label for m in d.metrics] == ["Competitor share (A)"]


def test_prompt_volume_spike():
    pts = series("prompt_volume", [1000, 1050, 980, 1010, 1020, 990, 1000, 1030, 3000])
    [d] = detect(pts)
    assert d.category is IncidentCategory.PROMPT_VOLUME_SPIKE
    assert d.metrics[0].delta_pct == pytest.approx(200, rel=0.05)
    assert d.priority.breakdown["prompt_demand"].value > 0.8


def test_factual_conflict_from_accuracy_and_conflict_count():
    pts = series("accuracy", [95, 96, 94, 95, 95, 96, 95, 60]) + series("factual_conflicts", [0, 0, 0, 0, 0, 0, 0, 3])
    [d] = detect(pts)
    assert d.category is IncidentCategory.FACTUAL_CONFLICT
    assert {m.key for m in d.metrics} == {"accuracy", "factual_conflicts"}


def test_event_metrics_use_zero_baseline_without_history():
    [d] = detect(series("new_competitor_content", [2]))
    assert d.category is IncidentCategory.NEW_COMPETITOR_CONTENT
    assert d.context["detection"][0]["baseline_source"] == "assumed_zero"
    assert d.confidence < 0.5
    assert detect(series("new_competitor_content", [0])) == []


def test_lost_citation_sources_and_stale_information():
    pts = series("cited_sources", [10, 10, 11, 10, 10, 10, 6]) + series("stale_sources", [0, 0, 0, 0, 0, 0, 2], cluster=CL2)
    cats = {d.category for d in detect(pts)}
    assert cats == {IncidentCategory.LOST_CITATION_SOURCE, IncidentCategory.STALE_INFORMATION}


def test_supporting_metric_alone_creates_nothing_but_attaches_when_primary_exists():
    pos = series("avg_position", [2.0, 2.1, 1.9, 2.0, 2.0, 2.1, 2.0, 4.5])
    assert detect(pos) == []
    [d] = detect(series("visibility", STABLE + [37]) + series("avg_position", [2.0] * 10 + [4.5]))
    assert "avg_position" in {m.key for m in d.metrics}


def test_clusters_are_isolated_and_sorted_by_priority():
    pts = (
        series("visibility", STABLE + [37], cluster=CL)
        + series("visibility", STABLE + [55], cluster=CL2)
        + series("prompt_volume", [2000] * 10 + [2000], cluster=CL)
    )
    drafts = detect(pts)
    assert [d.prompt_cluster_id for d in drafts] == [CL, CL2]
    assert drafts[0].priority.score > drafts[1].priority.score


def test_unrecognised_metrics_and_non_finite_values_are_ignored():
    pts = series("mystery_metric", [1, 2, 3, 4, 100]) + [
        SignalPoint("visibility", float("nan"), NOW, prompt_cluster_id=CL)
    ]
    assert detect(pts) == []


def test_kind_is_used_when_metric_name_is_unknown():
    pts = [
        SignalPoint("some_provider_metric_name", v, T0 + timedelta(days=i), kind="visibility", prompt_cluster_id=CL)
        for i, v in enumerate(STABLE + [37])
    ]
    assert len(detect(pts)) == 1


def test_priority_breakdown_flags_unknown_components_and_context_fills_known_ones():
    ctx = DetectionContext(
        cluster_topics={CL: "Pricing"},
        cluster_prompts={CL: ["best CRM pricing vs Acme", "CRM enterprise cost comparison"]},
        personas=[{"name": "CISO", "importance": 0.9}],
    )
    pts = series("visibility", STABLE + [37], raw={"persona": "CISO"}) + series("prompt_volume", [1000] * 11)
    [d] = detect(pts, context=ctx)
    b = d.priority.breakdown
    assert b["buyer_intent"].source == "heuristic" and b["buyer_intent"].value >= 0.8
    assert b["persona_importance"].value == pytest.approx(0.9)
    assert b["prompt_demand"].value == pytest.approx(1000 / 1500)
    assert b["competitive_displacement"].source == "default"
    assert "competitive_displacement" in d.context["unknown_priority_components"]
    assert d.severity in set(Severity)


def test_larger_drop_has_higher_priority_than_marginal_one():
    big = detect(series("visibility", STABLE + [30]))[0]
    small = detect(series("visibility", STABLE + [55]))[0]
    assert big.priority.score > small.priority.score


def test_deterministic():
    pts = series("visibility", STABLE + [37]) + series("competitor_share", [20] * 10 + [50], raw={"competitor": "A"})
    a, b = detect(pts), detect(list(reversed(pts)))
    assert [x.title for x in a] == [x.title for x in b]
    assert [x.priority.score for x in a] == [x.priority.score for x in b]


def test_custom_thresholds():
    pts = series("visibility", STABLE + [53])  # -9pp
    assert len(detect(pts)) == 1
    from dataclasses import replace

    from app.incidents.detector import RULES

    strict = DetectionConfig(rules={**RULES, "visibility": replace(RULES["visibility"], min_abs=10.0)})
    assert detect(pts, config=strict) == []


def test_historical_replay_cannot_see_a_later_drop():
    pts = series("visibility", STABLE + [37])  # drop on day 10
    before = historical_replay(pts, as_of=T0 + timedelta(days=9, hours=12))
    after = historical_replay(pts, as_of=NOW)
    assert before == {
        "mode": "historical_replay",
        "as_of": (T0 + timedelta(days=9, hours=12)).isoformat(),
        "would_detect": 0,
        "categories": [],
        "titles": [],
    }
    assert after["mode"] == "historical_replay" and after["would_detect"] == 1
    assert after["categories"] == ["visibility_drop"]


def test_filter_duplicates_matches_family_and_cluster():
    d = detect(series("competitor_share", [20] * 10 + [45], raw={"competitor": "A"}))
    assert len(d) == 1
    # an open visibility_drop incident in the same cluster blocks a competitor-gain draft (same family)
    assert filter_duplicates(d, [ExistingIncident(IncidentCategory.VISIBILITY_DROP.value, CL)]) == []
    # a different cluster or different family does not
    assert filter_duplicates(d, [ExistingIncident(IncidentCategory.VISIBILITY_DROP.value, CL2)]) == d
    assert filter_duplicates(d, [ExistingIncident(IncidentCategory.STALE_INFORMATION.value, CL)]) == d


# --- DB shell (uses the shared test database fixture) -----------------------------------------------------


async def _seed(session, values, metric="visibility", cluster_id=None, raw=None, org_id=None):
    from app.models.core import Signal

    for i, v in enumerate(values):
        session.add(Signal(
            org_id=org_id, kind=metric, source="profound", metric=metric, value=v, observed_at=T0 + timedelta(days=i),
            prompt_cluster_id=cluster_id, raw=raw or {},
        ))
    await session.flush()


async def _make_org_and_cluster(session):
    from app.models.core import Organization, PromptCluster

    org = Organization(name="Test Org", domain=f"{uuid.uuid4().hex[:8]}.example", personas=[], topics=[])
    session.add(org)
    await session.flush()
    cl = PromptCluster(org_id=org.id, topic="Enterprise SSO", prompts=["enterprise sso pricing"])
    session.add(cl)
    await session.flush()
    return org, cl


async def test_detect_incidents_persists_and_dedups(session):
    org, cl = await _make_org_and_cluster(session)
    await _seed(session, STABLE + [37], cluster_id=cl.id, org_id=org.id)
    created = await detect_incidents(session, org.id, now=NOW)
    assert len(created) == 1
    inc = created[0]
    assert inc.category == IncidentCategory.VISIBILITY_DROP.value
    assert inc.state == IncidentState.DETECTED.value
    assert inc.number is not None and inc.prompt_cluster_id == cl.id
    assert inc.metrics[0]["label"] == "Visibility" and inc.metrics[0]["unit"] == "pp"
    assert set(inc.priority_breakdown["components"]) >= {"prompt_demand", "buyer_intent", "remediation_feasibility"}
    assert inc.severity in {s.value for s in Severity} and 0 <= inc.priority <= 100
    assert inc.first_observed_at is not None
    # second run: the open incident suppresses a duplicate
    assert await detect_incidents(session, org.id, now=NOW) == []
    # once terminal (dismissed; closing from `detected` is not a machine edge), a persisting anomaly is reported again
    from app.incidents.state_machine import transition as _transition

    _transition(inc, IncidentState.DISMISSED, "test", "terminal")
    await session.flush()
    assert len(await detect_incidents(session, org.id, now=NOW)) == 1


async def test_detect_incidents_without_signals_returns_empty(session):
    org, _ = await _make_org_and_cluster(session)
    assert await detect_incidents(session, org.id, now=NOW) == []


def test_fixture_source_is_not_labeled_live():
    live = detect(series("visibility", STABLE + [37], source="profound"))
    fixture = detect(series("visibility", STABLE + [37], source="dev_fixture"))
    assert live and live[0].context["provenance"] == "live"
    assert fixture and fixture[0].context["provenance"] == "test_fixture"
    assert fixture[0].context["signature"]["primary_metric"] == "visibility"
    assert fixture[0].context["signature"]["direction"] == "negative"
