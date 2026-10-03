"""Unit tests for app.investigation.evidence_gate. Fixtures here are synthetic test doubles only."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from app.domain.enums import EvidenceStatus, EvidenceType
from app.investigation import evidence_gate as eg
from app.investigation.evidence_gate import EvidencePolicy, GateResult, confirm_aeo_root_cause

T0 = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)  # incident time (test fixture)


@dataclass
class Ev:
    """Stand-in for app.models.evidence.Evidence (duck-typed)."""
    id: str
    type: Any = EvidenceType.PROFOUND
    status: Any = EvidenceStatus.LIVE
    title: str = ""
    url: str | None = None
    excerpt: str | None = None
    content_hash: str | None = None
    observed_at: datetime | None = None
    support_score: float | None = 0.8
    contradiction_score: float | None = 0.0
    confidence: float | None = 0.8
    raw: dict = field(default_factory=dict)


@dataclass
class Hyp:
    title: str = "Competitor page update displaced our citation"
    rationale: str = "Competitor published a comparison page days before our citation share fell."
    confidence: float | None = 0.8
    status: str = "proposed"
    evidence_ids: list[str] = field(default_factory=list)
    category: str | None = None
    incident_at: datetime | None = T0


def profound(i="p1", **kw) -> Ev:
    return Ev(i, EvidenceType.PROFOUND, title="Visibility drop", observed_at=T0,
              raw={"metric": "visibility", "before": 0.61, "after": 0.37}, **kw)


def owned(i="o1", **kw) -> Ev:
    kw.setdefault("observed_at", T0 - timedelta(days=2))
    return Ev(i, EvidenceType.OWNED, title="Our SSO page", url="https://acme.test/sso",
              excerpt="Single sign-on is available on Enterprise only.", content_hash="abc", **kw)


def external(i="x1", **kw) -> Ev:
    kw.setdefault("observed_at", T0 - timedelta(days=1))
    return Ev(i, EvidenceType.EXTERNAL, title="Review site", url="https://reviews.test/acme",
              excerpt="Acme SSO listed as unavailable.", content_hash="def", **kw)


def citation(i="c1", **kw) -> Ev:
    return Ev(i, EvidenceType.EXTERNAL, title="Cited source", url="https://reviews.test/acme",
              excerpt="cited text", content_hash="h", observed_at=T0 - timedelta(days=1),
              raw={"kind": "citation"}, **kw)


def full_set() -> list[Ev]:
    return [profound(), owned(), external()]


def hyp_for(evs, **kw) -> Hyp:
    return Hyp(evidence_ids=[e.id for e in evs], **kw)


def gate(evs, hyp=None, policy=None, **kw) -> GateResult:
    return confirm_aeo_root_cause(hyp or hyp_for(evs), evs, policy, **kw)


# ---------------------------------------------------------------- happy path


def test_strong_evidence_confirms():
    r = gate(full_set())
    assert r.confirmed, r.reasons
    assert r.missing == []
    assert r.confidence == pytest.approx(0.8)
    assert set(r.supporting_ids) == {"p1", "o1", "x1"}
    assert r.contradictions == [] and r.contradicting_ids == []
    assert r.checks[eg.PROFOUND_SIGNAL]["ok"] and r.checks[eg.TIMESTAMP_ALIGNMENT]["ok"]


def test_result_is_serialisable_and_does_not_mutate_inputs():
    evs, h = full_set(), None
    h = hyp_for(evs)
    before = (h.status, list(h.evidence_ids), [e.support_score for e in evs])
    d = confirm_aeo_root_cause(h, evs).to_dict()
    assert d["confirmed"] is True
    assert (h.status, h.evidence_ids, [e.support_score for e in evs]) == before


def test_accepts_string_enums_and_dict_raw():
    evs = [replace(profound(), type="profound", status="live"), replace(owned(), type="owned")]
    assert gate(evs).confirmed


# ---------------------------------------------------------------- low evidence


def test_no_evidence_not_confirmed_lists_everything_missing():
    r = confirm_aeo_root_cause(Hyp(), [])
    assert not r.confirmed
    for code in (eg.PROFOUND_SIGNAL, eg.METRIC_CHANGE, eg.CONTENT_EVIDENCE, eg.TIMESTAMP_ALIGNMENT,
                 eg.AGGREGATE_CONFIDENCE, eg.CITED_EVIDENCE):
        assert code in r.missing
    assert len(r.reasons) == len(r.missing) and all(r.reasons)


def test_none_evidence_is_empty():
    assert not confirm_aeo_root_cause(Hyp(), None).confirmed  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("drop", "missing_code"),
    [("p1", eg.PROFOUND_SIGNAL), ("o1", eg.CONTENT_EVIDENCE)],
)
def test_each_required_source_type_is_needed(drop, missing_code):
    evs = [e for e in [profound(), owned()] if e.id != drop]
    if drop == "o1":
        evs = [profound()]
    r = gate(evs)
    assert not r.confirmed and missing_code in r.missing


def test_profound_only_lacks_content_and_alignment():
    r = gate([profound()])
    assert not r.confirmed
    assert eg.CONTENT_EVIDENCE in r.missing and eg.TIMESTAMP_ALIGNMENT in r.missing
    assert eg.PROFOUND_SIGNAL not in r.missing and eg.METRIC_CHANGE not in r.missing


def test_content_only_lacks_profound_and_metric():
    r = gate([owned()])
    assert not r.confirmed
    assert eg.PROFOUND_SIGNAL in r.missing and eg.METRIC_CHANGE in r.missing


def test_metric_change_requires_real_nonzero_delta():
    evs = [replace(profound(), raw={"metric": "visibility"}), owned()]
    assert eg.METRIC_CHANGE in gate(evs).missing
    evs = [replace(profound(), raw={"before": 0.5, "after": 0.5}), owned()]
    assert eg.METRIC_CHANGE in gate(evs).missing
    evs = [replace(profound(), raw={"metrics": [{"delta_pct": -7.3}]}), owned()]
    assert gate(evs).confirmed
    p = profound()
    p.metric_delta = -0.2  # attribute form
    p.raw = {}
    assert gate([p, owned()]).confirmed


def test_metric_change_can_come_from_a_non_profound_item():
    p = replace(profound(), raw={})
    o = replace(owned(), raw={"baseline": 10, "value": 14})
    r = gate([p, o])
    assert r.confirmed, r.reasons


def test_unscored_evidence_is_neutral_not_support():
    evs = [replace(profound(), support_score=None, confidence=None), owned()]
    r = gate(evs)
    assert eg.PROFOUND_SIGNAL in r.missing
    assert any(i["id"] == "p1" for i in r.ignored)


def test_below_support_threshold_is_not_support():
    evs = [replace(profound(), support_score=0.3), owned()]
    assert eg.PROFOUND_SIGNAL in gate(evs).missing


def test_confidence_fallback_when_support_score_missing():
    evs = [replace(profound(), support_score=None, confidence=0.9), owned()]
    assert gate(evs).confirmed


def test_low_aggregate_confidence_blocks():
    evs = [replace(e, support_score=0.55) for e in full_set()]
    r = gate(evs)
    assert not r.confirmed and r.missing == [eg.AGGREGATE_CONFIDENCE]
    assert r.confidence == pytest.approx(0.55)
    assert gate(evs, policy=EvidencePolicy(min_aggregate_confidence=0.5)).confirmed


def test_hypothesis_confidence_threshold_optional():
    evs = full_set()
    pol = EvidencePolicy(min_hypothesis_confidence=0.9)
    r = gate(evs, hyp_for(evs, confidence=0.5), pol)
    assert eg.HYPOTHESIS_CONFIDENCE in r.missing
    r = gate(evs, hyp_for(evs, confidence=None), pol)
    assert eg.HYPOTHESIS_CONFIDENCE in r.missing
    assert gate(evs, hyp_for(evs, confidence=0.95), pol).confirmed


# ---------------------------------------------------------------- unavailable / failed / inference


@pytest.mark.parametrize("status", [EvidenceStatus.UNAVAILABLE, EvidenceStatus.FAILED, "unavailable", "failed"])
def test_unavailable_or_failed_never_counts_even_with_high_scores(status):
    evs = [profound(), replace(owned(), status=status, support_score=1.0, confidence=1.0)]
    r = gate(evs)
    assert not r.confirmed and eg.CONTENT_EVIDENCE in r.missing
    assert "o1" not in r.supporting_ids
    assert any(i["id"] == "o1" for i in r.ignored)


def test_unavailable_cannot_be_forced_by_policy():
    pol = EvidencePolicy(excluded_statuses=frozenset())
    evs = [profound(), replace(owned(), status=EvidenceStatus.UNAVAILABLE)]
    assert not gate(evs, policy=pol).confirmed


def test_failed_item_does_not_prop_up_the_aggregate():
    evs = full_set() + [replace(external("x2"), status="failed", support_score=1.0)]
    r = gate(evs, hyp_for(full_set()))
    assert r.confirmed and "x2" not in r.supporting_ids and r.confidence == pytest.approx(0.8)


def test_unavailable_contradiction_does_not_block_but_is_ignored():
    evs = full_set() + [Ev("u1", EvidenceType.EXTERNAL, status="unavailable", contradiction_score=0.99)]
    r = gate(evs, hyp_for(full_set()))
    assert r.confirmed and r.contradictions == []


def test_inference_evidence_never_supports():
    inf = Ev("i1", EvidenceType.INFERENCE, support_score=0.99, confidence=0.99, observed_at=T0,
             excerpt="LLM guess", content_hash="z", raw={"delta": 5})
    r = gate([inf])
    assert not r.confirmed and r.supporting_ids == []
    assert any(i["id"] == "i1" for i in r.ignored)
    # inference can't fill in for a missing content leg
    r = gate([profound(), inf])
    assert eg.CONTENT_EVIDENCE in r.missing


def test_content_without_excerpt_or_hash_is_not_grounded():
    bare = Ev("o1", EvidenceType.OWNED, url="https://acme.test", observed_at=T0, support_score=0.9)
    r = gate([profound(), bare])
    assert eg.CONTENT_EVIDENCE in r.missing
    assert any("grounded" in i["reason"] for i in r.ignored)


# ---------------------------------------------------------------- contradictions


def test_strong_contradiction_blocks_and_is_preserved():
    evs = full_set() + [Ev("k1", EvidenceType.OWNED, title="Page says SSO on all plans", excerpt="all plans",
                           url="https://acme.test/pricing", support_score=0.0, contradiction_score=0.9)]
    r = gate(evs, hyp_for(full_set()))  # hypothesis did not cite k1
    assert not r.confirmed and eg.CONTRADICTIONS in r.missing
    assert r.contradicting_ids == ["k1"]
    assert r.contradictions[0]["score"] == 0.9 and r.contradictions[0]["url"] == "https://acme.test/pricing"
    assert "k1" in " ".join(r.reasons)


def test_weak_contradiction_penalises_but_does_not_block():
    evs = full_set() + [Ev("k1", EvidenceType.EXTERNAL, excerpt="x", support_score=0.0, contradiction_score=0.55)]
    r = gate(evs, hyp_for(full_set()))
    assert r.contradicting_ids == ["k1"]
    assert r.confidence == pytest.approx(0.8 - 0.5 * 0.55)
    # 0.525 < 0.6 default minimum, so aggregate confidence fails (contradiction itself does not block)
    assert r.missing == [eg.AGGREGATE_CONFIDENCE]
    ok = gate(evs, hyp_for(full_set()), EvidencePolicy(min_aggregate_confidence=0.5))
    assert ok.confirmed and ok.contradicting_ids == ["k1"]
    assert ok.checks[eg.CONTRADICTIONS]["ids"] == ["k1"]


def test_contradicting_item_cannot_be_counted_as_support():
    both = replace(owned(), support_score=0.6, contradiction_score=0.8)
    r = gate([profound(), both])
    assert "o1" not in r.supporting_ids and r.contradicting_ids == ["o1"]
    assert not r.confirmed


def test_block_threshold_is_configurable():
    evs = full_set() + [Ev("k1", EvidenceType.EXTERNAL, excerpt="x", support_score=0.0, contradiction_score=0.9)]
    pol = EvidencePolicy(contradiction_block_threshold=0.95, contradiction_penalty=0.0)
    r = gate(evs, hyp_for(full_set()), pol)
    assert r.confirmed and r.contradicting_ids == ["k1"]


# ---------------------------------------------------------------- timestamp alignment


def test_evidence_observed_long_after_incident_is_not_aligned():
    evs = [profound(), owned(observed_at=T0 + timedelta(days=5))]
    r = gate(evs)
    assert not r.confirmed and r.missing == [eg.TIMESTAMP_ALIGNMENT]
    assert r.checks[eg.TIMESTAMP_ALIGNMENT]["misaligned"] == ["o1"]


def test_evidence_just_after_incident_within_tolerance_is_aligned():
    assert gate([profound(), owned(observed_at=T0 + timedelta(hours=6))]).confirmed


def test_evidence_far_before_lookback_is_not_aligned():
    r = gate([profound(), owned(observed_at=T0 - timedelta(days=90))])
    assert eg.TIMESTAMP_ALIGNMENT in r.missing
    pol = EvidencePolicy(alignment_lookback=None)
    assert gate([profound(), owned(observed_at=T0 - timedelta(days=90))], policy=pol).confirmed


def test_missing_observed_at_fails_alignment():
    r = gate([profound(), owned(observed_at=None)])
    assert eg.TIMESTAMP_ALIGNMENT in r.missing and r.checks[eg.TIMESTAMP_ALIGNMENT]["undated"] == ["o1"]


def test_one_aligned_item_is_enough():
    r = gate([profound(), owned(observed_at=T0 + timedelta(days=9)), external()])
    assert r.confirmed and r.checks[eg.TIMESTAMP_ALIGNMENT]["ids"] == ["x1"]


def test_naive_and_iso_string_timestamps_are_handled():
    h = replace(hyp_for([profound(), owned()]), incident_at="2026-09-20T12:00:00Z")
    evs = [profound(), replace(owned(), observed_at=datetime(2026, 9, 19, 12, 0))]  # noqa: DTZ001 naive == UTC
    assert confirm_aeo_root_cause(h, evs).confirmed
    evs = [profound(), replace(owned(), observed_at="2026-09-19T12:00:00+00:00")]
    assert confirm_aeo_root_cause(h, evs).confirmed


def test_incident_at_kwarg_overrides_and_fallback_anchor_is_profound_time():
    evs = [profound(), owned()]
    h = replace(hyp_for(evs), incident_at=None)
    r = confirm_aeo_root_cause(h, evs)
    assert r.confirmed and r.checks[eg.TIMESTAMP_ALIGNMENT]["anchor_source"] == "earliest_profound_observation"
    r = confirm_aeo_root_cause(h, evs, incident_at=T0 - timedelta(days=30))
    assert eg.TIMESTAMP_ALIGNMENT in r.missing


def test_no_anchor_at_all_fails_alignment():
    h = replace(hyp_for([owned()]), incident_at=None)
    r = confirm_aeo_root_cause(h, [owned()])
    assert eg.TIMESTAMP_ALIGNMENT in r.missing
    assert "cannot align" in " ".join(r.reasons)


def test_incident_time_read_from_nested_incident_object():
    @dataclass
    class Inc:
        first_observed_at: datetime = T0
        category: str = "visibility_drop"

    @dataclass
    class H2(Hyp):
        incident: Any = None
        incident_at: datetime | None = None

    evs = full_set()
    assert confirm_aeo_root_cause(H2(evidence_ids=["p1", "o1", "x1"], incident=Inc()), evs).confirmed


# ---------------------------------------------------------------- citations


def test_citation_required_for_citation_categories():
    evs = full_set()
    h = hyp_for(evs, category="competitor_citation_gain")
    r = gate(evs, h)
    assert eg.CITATION_EVIDENCE in r.missing
    evs2 = [*evs, citation()]
    assert gate(evs2, hyp_for(evs2, category="competitor_citation_gain")).confirmed


def test_citation_not_required_for_other_categories_and_reports_not_applicable():
    r = gate(full_set(), hyp_for(full_set(), category="visibility_drop"))
    assert r.confirmed and r.checks[eg.CITATION_EVIDENCE]["applicable"] is False


def test_citation_requirement_forced_by_policy_or_kwarg():
    evs = full_set()
    assert eg.CITATION_EVIDENCE in gate(evs, policy=EvidencePolicy(require_citation_evidence=True)).missing
    assert eg.CITATION_EVIDENCE in gate(evs, category="lost_citation_source").missing
    assert gate(evs, hyp_for(evs, category="lost_citation_source"),
                EvidencePolicy(require_citation_evidence=False)).confirmed


def test_citation_needs_url_and_citation_marker_and_usable_status():
    no_url = replace(citation(), url=None)
    dead = replace(citation(), status="unavailable")
    plain = replace(external("x9"), url="https://reviews.test/p")  # has url but not a citation
    for bad in (no_url, dead, plain):
        evs = [*full_set(), bad]
        assert eg.CITATION_EVIDENCE in gate(evs, hyp_for(evs, category="lost_citation_source")).missing
    flagged = replace(external("x9"), url="https://reviews.test/p")
    flagged.is_citation = True
    evs = [*full_set(), flagged]
    assert gate(evs, hyp_for(evs, category="lost_citation_source")).confirmed


# ---------------------------------------------------------------- rationale, citations to evidence, state


def test_missing_or_short_rationale_blocks():
    evs = full_set()
    for rat in ("", "   ", None, "too short"):
        r = gate(evs, hyp_for(evs, rationale=rat))
        assert not r.confirmed and r.missing == [eg.RATIONALE], rat
    assert gate(evs, hyp_for(evs, rationale="x"), EvidencePolicy(require_rationale=False)).confirmed


def test_hypothesis_must_cite_evidence_and_ids_must_exist():
    evs = full_set()
    r = gate(evs, Hyp(evidence_ids=[]))
    assert eg.CITED_EVIDENCE in r.missing
    r = gate(evs, Hyp(evidence_ids=["p1", "o1", "ghost"]))
    assert eg.CITED_EVIDENCE in r.missing and "ghost" in " ".join(r.reasons)
    assert gate(evs, Hyp(evidence_ids=[]), EvidencePolicy(require_cited_evidence=False)).confirmed


def test_uncited_evidence_does_not_support():
    evs = full_set()
    r = gate(evs, Hyp(evidence_ids=["p1"]))
    assert r.supporting_ids == ["p1"] and eg.CONTENT_EVIDENCE in r.missing


def test_rejected_hypothesis_cannot_be_confirmed():
    evs = full_set()
    r = gate(evs, hyp_for(evs, status="rejected"))
    assert not r.confirmed and eg.HYPOTHESIS_STATE in r.missing


def test_hypothesis_status_enum_supported():
    from app.domain.enums import HypothesisStatus

    evs = full_set()
    assert not gate(evs, hyp_for(evs, status=HypothesisStatus.REJECTED)).confirmed
    assert gate(evs, hyp_for(evs, status=HypothesisStatus.PROPOSED)).confirmed


# ---------------------------------------------------------------- policy knobs


def test_policy_can_relax_requirements():
    pol = EvidencePolicy(require_profound_signal=False, require_metric_change=False,
                         require_timestamp_alignment=False)
    r = gate([owned()], policy=pol)
    assert r.confirmed, r.reasons


def test_policy_can_tighten_requirements():
    pol = EvidencePolicy(min_aggregate_confidence=0.95)
    assert gate(full_set(), policy=pol).missing == [eg.AGGREGATE_CONFIDENCE]


def test_policy_is_frozen():
    with pytest.raises(AttributeError):
        EvidencePolicy().min_aggregate_confidence = 0.1  # type: ignore[misc]


def test_competitor_content_counts_by_default_but_can_be_excluded():
    comp = replace(owned("c9"), type=EvidenceType.COMPETITOR, title="Competitor pricing page")
    evs = [profound(), comp]
    assert gate(evs).confirmed
    pol = EvidencePolicy(content_types=frozenset({"owned", "external"}))
    assert eg.CONTENT_EVIDENCE in gate(evs, policy=pol).missing


def test_stale_and_changed_status_still_usable():
    evs = [profound(), replace(owned(), status=EvidenceStatus.STALE), replace(external(), status=EvidenceStatus.CHANGED)]
    assert gate(evs, hyp_for(evs)).confirmed


def test_dict_evidence_is_accepted():
    evs = [
        {"id": "p1", "type": "profound", "status": "live", "support_score": 0.9, "observed_at": T0,
         "raw": {"delta": -12.0}},
        {"id": "o1", "type": "owned", "status": "live", "support_score": 0.8, "excerpt": "text",
         "observed_at": T0 - timedelta(days=1)},
    ]
    r = confirm_aeo_root_cause({"rationale": "A" * 40, "evidence_ids": ["p1", "o1"], "incident_at": T0}, evs)
    assert r.confirmed, r.reasons


def test_confirmed_reasons_describe_passes_and_failed_reasons_describe_failures():
    ok = gate(full_set())
    assert ok.confirmed and ok.reasons and ok.missing == []
    bad = gate([profound()])
    assert len(bad.reasons) == len(bad.missing)
