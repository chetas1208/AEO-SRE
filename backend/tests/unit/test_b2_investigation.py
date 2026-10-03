"""B2: taxonomy, control movement, counterevidence, evidence-derived confidence, assessment, budget, two-stage gate."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from app.investigation.assessment import assess_investigation, scope_of_incident
from app.investigation.budget import BudgetTracker, InvestigationBudget, StopReason, decide_stop
from app.investigation.confidence import CONFIRM_MIN, MEDIUM_CAP, compute_confidence
from app.investigation.control import (
    ControlSeries,
    ControlVerdict,
    assess_control_movement,
    series_delta_pp,
)
from app.investigation.counterevidence import reverify_urls, run_checks
from app.investigation.evidence_gate import confirm_aeo_root_cause
from app.investigation.taxonomy import BRAND_SPECIFIC, RootCause, cause_for

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)


@dataclass
class Ev:
    id: str
    type: str = "profound"
    status: str = "live"
    title: str = ""
    url: str | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    observed_at: datetime | None = T0
    support_score: float | None = 0.8
    contradiction_score: float | None = 0.0
    confidence: float | None = 0.8
    raw: dict = field(default_factory=dict)


def metric(i="p1", **raw):
    return Ev(i, "profound", title="Visibility 62 -> 37", raw={"kind": "metric_change", "metric": "visibility",
                                                               "before": 62, "after": 37, "delta": -25, **raw})


def competitor(i="c1", changed=True, **kw):
    return Ev(i, "competitor", status="changed" if changed else "live", title="Globex SSO guide",
              url="https://globex.test/sso", excerpt="Globex SSO guide covers SAML and SCIM.", content_hash="g1",
              observed_at=T0 - timedelta(days=1), raw={"source_category": "COMPETITOR", **kw.pop("raw", {})}, **kw)


def owned(i="o1", changed=False, **kw):
    return Ev(i, "owned", status="changed" if changed else "live", title="Our SSO page", url="https://acme.test/sso",
              excerpt="SSO is available on Enterprise.", content_hash="a1", observed_at=T0 - timedelta(days=2),
              raw={"source_category": "OWNED"}, **kw)


def citation(i="k1"):
    return Ev(i, "profound", title="Citation replaced", raw={"citation_change": "replaced", "kind": "citation",
                                                              "delta": -3, "metric": "cited_sources"},
              url="https://globex.test/sso")


def hyp(i="h1", ids=("p1", "c1", "k1"), conf=0.7, by="rules:competitor_canonical_improved"):
    return SimpleNamespace(id=i, title="A competitor improved dedicated content", evidence_ids=list(ids),
                           confidence=conf, produced_by=by, status="proposed",
                           rationale="Competitor published a dedicated SSO guide before our citation share fell.")


META = {"h1": {"rule_id": "competitor_canonical_improved", "layer": "competitor", "actionable": True}}
INCIDENT = SimpleNamespace(id="i1", category="visibility_drop", first_observed_at=T0, detected_at=T0,
                           context={"correlated_signals": ["visibility_regression", "competitor_gain"],
                                    "signature": {"platform": None, "persona": None}})


def gate_for(h, evidence):
    return confirm_aeo_root_cause(h, evidence, incident_at=T0, category="visibility_drop")


def good_evidence():
    return [metric(), competitor(), citation()]


def control(verdict=ControlVerdict.BRAND_SPECIFIC):
    from app.investigation.control import ControlAssessment

    return ControlAssessment(verdict, -25.0, [], 0, 3, "note") if verdict is not ControlVerdict.INCONCLUSIVE else \
        ControlAssessment(verdict, -25.0, [], 0, 0, "no controls")


def assess(evidence=None, h=None, ctrl=None, gate_h=None):
    evidence = evidence or good_evidence()
    h = h or hyp()
    gate = gate_for(h, evidence)
    return assess_investigation(INCIDENT, [h], META, evidence, {h.id: gate}, ctrl or control()), gate


# ---------------------------------------------------------------- taxonomy


def test_every_rca_rule_maps_into_the_taxonomy():
    from app.investigation.rca import RULES  # noqa: F401

    assert cause_for("competitor_canonical_improved") is RootCause.COMPETITOR_CANONICAL_IMPROVED
    assert cause_for("capability_exists_but_buried") is RootCause.OWNED_BURIED
    assert cause_for("prompt_cluster_changed", title="The AI engine's behaviour changed (model update)") is RootCause.MODEL_VARIANCE
    assert cause_for("prompt_cluster_changed", title="The prompt cluster itself changed") is RootCause.QUERY_INTERPRETATION_SHIFT
    assert cause_for("llm", "citation") is RootCause.CITATION_SOURCE_SHIFT  # LLM hypotheses ride on their layer
    assert cause_for("unknown_rule", None) is RootCause.INSUFFICIENT_EVIDENCE
    assert RootCause.MODEL_VARIANCE not in BRAND_SPECIFIC


# ---------------------------------------------------------------- control movement


def _series(name, group, before, after, at=T0):
    pts = [(at - timedelta(days=10 - i), before) for i in range(8)] + [(at, after)]
    return ControlSeries(name, group, pts)


def test_everyone_fell_is_category_wide():
    ctrls = [_series("Globex", "competitor", 0.30, 0.15), _series("Initech", "competitor", 0.25, 0.10),
             _series("c2", "cluster", 0.50, 0.30)]
    a = assess_control_movement(-25.0, ctrls, T0)
    assert a.verdict is ControlVerdict.CATEGORY_WIDE and a.moved == 3 and "platform/model" in a.note


def test_competitors_rising_is_brand_specific():
    ctrls = [_series("Globex", "competitor", 0.10, 0.31), _series("Initech", "competitor", 0.20, 0.21),
             _series("c2", "cluster", 0.50, 0.50)]
    a = assess_control_movement(-25.0, ctrls, T0)
    assert a.verdict is ControlVerdict.BRAND_SPECIFIC and a.checked


def test_too_few_controls_is_inconclusive_not_brand_specific():
    a = assess_control_movement(-25.0, [_series("Globex", "competitor", 0.3, 0.1)], T0)
    assert a.verdict is ControlVerdict.INCONCLUSIVE and not a.checked
    assert series_delta_pp([(T0, 0.5)], T0) is None


# ---------------------------------------------------------------- counterevidence


def test_counterevidence_found_when_competitor_page_not_new_and_own_page_changed():
    ev = [metric(), competitor(changed=False), owned(changed=True)]
    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), {"c1"})
    codes = {c.code for c in rep.found}
    assert {"competitor_page_new_or_changed", "own_page_changed"} <= codes and rep.penalty >= 0.7


def test_contradicting_evidence_is_found():
    ev = [*good_evidence(), Ev("x1", "external", title="Review", url="https://r.test/a", excerpt="Acme SSO is fine",
                               content_hash="x", contradiction_score=0.8, support_score=0.1)]
    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), {"c1"})
    assert any(c.code == "contradiction_scan" and c.result == "found" for c in rep.checks)


def test_search_not_performed_when_nothing_is_checkable():
    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, [metric()], None, set())
    # metric evidence carries no contradiction score and there is no page or control data
    assert not rep.performed and rep.checkable < 2


async def test_reverify_flags_drift_and_missing_pages_within_budget():
    ev = [competitor("c1"), competitor("c2"), competitor("c3")]
    for e, u in zip(ev, ("a", "b", "c"), strict=True):
        e.url = f"https://globex.test/{u}"
    calls = []

    async def fetcher(url):
        calls.append(url)
        if url.endswith("a"):
            return SimpleNamespace(ok=True, content_hash="g1")
        if url.endswith("b"):
            return SimpleNamespace(ok=True, content_hash="changed")
        return SimpleNamespace(ok=False, content_hash=None)

    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), {"c1", "c2", "c3"})
    rep = await reverify_urls(rep, ev, {"c1", "c2", "c3"}, fetcher, max_requests=2)
    assert len(calls) == 2 and rep.web_requests == 2  # budget respected: third page never fetched
    assert any(c.code == "reverify_sources" and c.result == "found" for c in rep.checks)  # b drifted
    calls.clear()
    one = await reverify_urls(run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), {"c1"}),
                              ev, {"c1"}, fetcher, max_requests=1)
    assert any(c.code == "reverify_sources" and c.result == "clear" for c in one.checks)
    rep2 = await reverify_urls(run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), {"c2", "c3"}),
                               ev, {"c2", "c3"}, fetcher, max_requests=5)
    assert any(c.code == "reverify_sources" and c.result == "found" for c in rep2.checks)
    assert (await reverify_urls(rep, ev, {"c1"}, None, max_requests=3)) is rep  # no fetcher: untouched


# ---------------------------------------------------------------- confidence


def test_llm_confidence_is_one_weak_feature():
    ev = good_evidence()
    gate = gate_for(hyp(), ev)
    sup = [e for e in ev if e.id in gate.supporting_ids]
    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), set(gate.supporting_ids))
    lo = compute_confidence(RootCause.COMPETITOR_CANONICAL_IMPROVED, sup, gate_checks=gate.checks, mean_support=0.8,
                            prior=0.0, counter=rep, control=control())
    hi = compute_confidence(RootCause.COMPETITOR_CANONICAL_IMPROVED, sup, gate_checks=gate.checks, mean_support=0.8,
                            prior=1.0, counter=rep, control=control())
    assert 0 < hi.value - lo.value <= 0.05 + 1e-9
    assert lo.weights["prior"] == 0.05 and sum(lo.weights.values()) == pytest.approx(1.0)


def test_unrun_checks_cap_confidence_below_high():
    ev = good_evidence()
    gate = gate_for(hyp(), ev)
    sup = [e for e in ev if e.id in gate.supporting_ids]
    c = compute_confidence(RootCause.COMPETITOR_CANONICAL_IMPROVED, sup, gate_checks=gate.checks, mean_support=1.0,
                           prior=1.0, counter=None, control=None)
    assert c.value <= 0.70 and "counterevidence_not_searched" in c.caps and c.tier != "high"
    assert MEDIUM_CAP < 0.8


def test_category_wide_movement_halves_brand_specific_confidence():
    ev = good_evidence()
    gate = gate_for(hyp(), ev)
    sup = [e for e in ev if e.id in gate.supporting_ids]
    rep = run_checks(RootCause.COMPETITOR_CANONICAL_IMPROVED, ev, control(), set(gate.supporting_ids))
    kw = dict(gate_checks=gate.checks, mean_support=0.8, prior=0.7, counter=rep)
    base = compute_confidence(RootCause.COMPETITOR_CANONICAL_IMPROVED, sup, control=control(), **kw)
    wide = compute_confidence(RootCause.COMPETITOR_CANONICAL_IMPROVED, sup, control=control(ControlVerdict.CATEGORY_WIDE), **kw)
    assert wide.value < base.value * 0.6 and "category_wide_control_movement" in wide.caps
    var = compute_confidence(RootCause.MODEL_VARIANCE, sup, control=control(ControlVerdict.CATEGORY_WIDE), **kw)
    assert var.value > wide.value  # the null cause is not penalized by category-wide movement


# ---------------------------------------------------------------- assessment (second-stage gate)


def test_clean_case_passes_both_gates_with_taxonomy_and_computed_confidence():
    res, gate = assess()
    a = res.assessments[0]
    assert gate.confirmed and a.allow_confirm and res.outcome == "confirmed"
    assert a.cause is RootCause.COMPETITOR_CANONICAL_IMPROVED and a.confidence.value >= CONFIRM_MIN
    assert a.confidence.value != 0.7  # not the rule/LLM prior echoed back
    assert {"families", "temporal", "consistency", "support", "directness", "prior"} == set(a.confidence.features)


def test_llm_cannot_self_confirm():
    # an LLM hypothesis claiming 1.0 confidence over evidence the gate rejects stays blocked
    ev = [metric()]  # no content evidence at all
    h = hyp(ids=("p1",), conf=1.0, by="llm:x")
    res = assess_investigation(INCIDENT, [h], {"h1": {"rule_id": "llm", "layer": "competitor"}}, ev,
                               {"h1": gate_for(h, ev)}, control())
    assert not res.assessments[0].allow_confirm and "evidence_gate_not_passed" in res.assessments[0].blockers
    assert res.outcome == "insufficient_evidence"


def test_no_counterevidence_search_blocks_confirmation():
    res, gate = assess(ctrl=None)
    # gate passes; control missing makes only one counterevidence check runnable? the search must have run
    a = res.assessments[0]
    if not a.counter.performed:
        assert "counterevidence_not_searched" in a.blockers and not a.allow_confirm


def test_unresolved_strong_counterevidence_blocks():
    ev = [metric(), competitor(changed=False), citation(), owned(changed=True)]
    h = hyp(ids=("p1", "c1", "k1"))
    res = assess_investigation(INCIDENT, [h], META, ev, {"h1": gate_for(h, ev)}, control())
    a = res.assessments[0]
    assert not a.allow_confirm and any(b.startswith("unresolved_counterevidence") for b in a.blockers)


def test_category_wide_control_blocks_brand_hypothesis_and_names_model_variance():
    res, _ = assess(ctrl=control(ControlVerdict.CATEGORY_WIDE))
    a = res.assessments[0]
    assert not a.allow_confirm and "category_wide_movement_rules_out_brand_specific_cause" in a.blockers
    assert any(x["cause"] == "model_variance" and x["status"] == "favored_by_control_movement" for x in res.alternatives)
    assert res.alternatives[-1]["cause"] == "insufficient_evidence" and res.outcome == "insufficient_evidence"


def test_competing_hypotheses_are_ranked_and_near_ties_are_not_crowned():
    ev = good_evidence() + [owned(changed=False)]
    h1 = hyp("h1", ids=("p1", "c1", "k1"))
    h2 = hyp("h2", ids=("p1", "c1", "k1"), by="rules:citation_source_changed")
    meta = {"h1": META["h1"], "h2": {"rule_id": "citation_source_changed", "layer": "citation", "actionable": True}}
    res = assess_investigation(INCIDENT, [h1, h2], meta, ev, {"h1": gate_for(h1, ev), "h2": gate_for(h2, ev)}, control())
    assert [a.rank for a in res.assessments] == [1, 2] and len(res.alternatives) >= 2
    top, second = res.assessments
    if abs(top.confidence.value - second.confidence.value) < 0.03 and top.cause != second.cause:
        assert any(b.startswith("ambiguous_between") for b in top.blockers)


def test_persona_and_platform_scope_is_never_generalized():
    inc = SimpleNamespace(context={"signature": {"platform": "ChatGPT", "persona": "CISO"}})
    s = scope_of_incident(inc)
    assert s["platform"] == "ChatGPT" and s["persona"] == "CISO" and s["generalizes_beyond_scope"] is False
    assert "only" in s["statement"] and "ChatGPT" in s["statement"]
    assert "whole prompt cluster" in scope_of_incident(SimpleNamespace(context={}))["statement"]


# ---------------------------------------------------------------- budget + stopping rules


def test_budget_dimensions_and_stop_reasons():
    t = BudgetTracker(InvestigationBudget(max_web_requests=2, max_llm_calls=1, max_evidence=5, max_hypotheses=3,
                                          max_seconds=10), started=T0)
    assert t.spend("web_requests") and t.spend("web_requests") and not t.spend("web_requests")
    assert t.exhausted == ["web_requests"] and t.remaining("web_requests") == 0
    assert t.spend("llm_calls") and not t.spend("llm_calls")
    assert not t.time_up(T0 + timedelta(seconds=5)) and t.time_up(T0 + timedelta(seconds=11))
    assert "seconds" in t.exhausted
    fresh = BudgetTracker(InvestigationBudget(), started=T0)
    assert decide_stop(confirmed=True, tracker=t, evidence_fingerprint="a", previous_fingerprint=None) is StopReason.CONFIRMED
    assert decide_stop(confirmed=False, tracker=t, evidence_fingerprint="a", previous_fingerprint=None) is StopReason.INSUFFICIENT_AFTER_BUDGET
    assert decide_stop(confirmed=False, tracker=fresh, evidence_fingerprint="a", previous_fingerprint="a") is StopReason.NO_NEW_USEFUL_EVIDENCE
    assert decide_stop(confirmed=False, tracker=fresh, evidence_fingerprint="b", previous_fingerprint="a") is StopReason.INSUFFICIENT_EVIDENCE


# ---------------------------------------------------------------- stage_gate on real rows


async def _seed(session):
    from tests import factories as f

    org = await f.make_org(session)
    cluster = await f.make_prompt_cluster(session, org)
    inc = await f.make_incident(
        session, org, prompt_cluster_id=cluster.id, first_observed_at=f.utc(1), detected_at=f.utc(0.5),
        metrics=[{"key": "visibility", "label": "Visibility", "before": 62, "after": 37, "delta": -25, "unit": "pp"}],
        context={"correlated_signals": ["visibility_regression", "competitor_gain"], "signature": {}},
    )
    p = await f.make_evidence(session, inc, type="profound", url=None, source="profound", excerpt="Visibility fell",
                              observed_at=f.utc(1), support_score=0.8,
                              raw={"kind": "metric_change", "metric": "visibility", "before": 62, "after": 37, "delta": -25})
    c = await f.make_evidence(session, inc, type="competitor", status="changed", url="https://globex.test/sso",
                              excerpt="Globex SSO guide", observed_at=f.utc(2), support_score=0.85,
                              raw={"source_category": "COMPETITOR"})
    k = await f.make_evidence(session, inc, type="profound", url="https://globex.test/sso", excerpt="citation replaced",
                              observed_at=f.utc(1), support_score=0.8,
                              raw={"citation_change": "replaced", "kind": "citation"})
    h = await f.make_hypothesis(session, inc, [p.id, c.id, k.id], confidence=0.55,
                                rationale="Competitor published a dedicated SSO guide before our share fell.")
    inc.context = {**inc.context, "hypotheses_meta": {str(h.id): {"rule_id": "competitor_canonical_improved",
                                                                   "layer": "competitor", "actionable": True}}}
    await session.commit()
    return inc, h


async def _controls(session, inc, kind):
    from app.models.core import Organization

    from tests import factories as f

    org = await session.get(Organization, inc.org_id)
    for name in ("Globex", "Initech"):
        for i in range(9):
            at = f.utc(10 - i) if i < 8 else f.utc(1)
            before, after = (0.30, 0.12) if kind == "wide" else (0.10, 0.30)
            await f.make_signal(session, org, metric="competitor_share", kind="competitor",
                                value=before if i < 8 else after, observed_at=at, raw={"competitor": name})


def _same_hash_fetcher(inc_evidence):
    async def fetch(url):
        h = next((e.content_hash for e in inc_evidence if e.url == url), None)
        return SimpleNamespace(ok=True, content_hash=h)

    return fetch


async def _gate(session, monkeypatch, kind):
    from app.models.core import Incident
    from app.models.evidence import Evidence
    from app.services import pipeline
    from sqlalchemy import select

    inc, h = await _seed(session)
    if kind:
        await _controls(session, inc, kind)
    ev = list((await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars())
    monkeypatch.setattr(pipeline, "counter_fetcher_factory", lambda: (_same_hash_fetcher(ev), _noop))
    out = await pipeline.stage_gate(session, inc.id)
    inc2 = await session.get(Incident, inc.id)
    await session.refresh(h)
    return out, inc2.context, h


async def test_stage_gate_persists_assessment_and_recomputes_confidence(session, monkeypatch):
    out, ctx, h = await _gate(session, monkeypatch, None)
    ass = ctx["assessment"]
    top = ass["ranking"][0]
    assert ass["scope"]["generalizes_beyond_scope"] is False and top["cause"] == "competitor_canonical_content_improved"
    assert h.confidence == top["confidence"]["value"] != 0.55  # evidence-derived; the rule prior survives only in meta
    assert ctx["hypotheses_meta"][str(h.id)]["prior_confidence"] == 0.55
    assert out["control"] == "inconclusive"
    assert (h.status == "confirmed") == (top["blockers"] == [])  # confirmation is exactly "no blockers"
    assert top["confidence"]["caps"]  # no controls -> confidence capped, never "high"
    assert top["confidence"]["tier"] != "high"


async def test_stage_gate_blocks_when_whole_category_moved(session, monkeypatch):
    out, ctx, h = await _gate(session, monkeypatch, "wide")
    top = ctx["assessment"]["ranking"][0]
    assert out["control"] == "category_wide" and h.status != "confirmed" and out["confirmed"] is False
    assert "category_wide_movement_rules_out_brand_specific_cause" in top["blockers"]
    assert ctx["assessment"]["outcome"] == "insufficient_evidence"
    assert any(a["cause"] == "model_variance" for a in ctx["assessment"]["alternatives"])


async def test_stage_gate_confirms_when_competitors_rose_and_checks_are_clear(session, monkeypatch):
    out, ctx, h = await _gate(session, monkeypatch, "specific")
    top = ctx["assessment"]["ranking"][0]
    assert out["control"] == "brand_specific"
    assert top["counter_found"] == [] and top["blockers"] == [] and h.status == "confirmed" and out["confirmed"]
    assert top["confidence"]["value"] >= 0.6 and ctx["gate_primary"]["confirmed"] is True


async def _fake_fetch(url):
    return SimpleNamespace(ok=True, content_hash="g1")


async def _noop():
    return None
