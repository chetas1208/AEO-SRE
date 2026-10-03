"""Unit tests for Intervention Priority scoring (pure functions)."""

from __future__ import annotations

import math

import pytest
from app.domain.enums import IncidentCategory, Severity
from app.incidents.priority import (
    COMPONENTS,
    PriorityConfig,
    SeverityThresholds,
    buyer_intent_from_prompts,
    compute_priority,
    demand_from_volume,
    displacement_from_competitor_gain,
    feasibility_for_category,
    is_below_action_threshold,
    persona_importance_from_config,
    severity_from_priority,
)


def full(**over):
    base = dict.fromkeys(COMPONENTS, 0.7)
    base.update(over)
    return base


def test_all_components_equal_gives_that_value_times_100():
    r = compute_priority(**full())
    assert r.score == pytest.approx(70.0, abs=1e-3)
    assert set(r.breakdown) == set(COMPONENTS)
    for c in r.breakdown.values():
        assert c.value == pytest.approx(0.7) and c.display == pytest.approx(70.0) and c.source == "measured"
        assert c.weight == pytest.approx(1 / 7)


def test_multiplicative_semantics_zero_kills_score_and_no_compensation():
    assert compute_priority(**full(evidence_confidence=0.0)).score == 0.0
    assert compute_priority(**full(buyer_intent=0.1, prompt_demand=1.0)).score < compute_priority(**full()).score


def test_monotonic_in_each_component():
    base = compute_priority(**full(buyer_intent=0.4)).score
    for name in COMPONENTS:
        lo = compute_priority(**full(**{name: 0.3})).score
        hi = compute_priority(**full(**{name: 0.9})).score
        assert lo < hi
    assert base < compute_priority(**full()).score


def test_score_bounds_and_clamping():
    assert compute_priority(**dict.fromkeys(COMPONENTS, 1.0)).score == pytest.approx(100.0)
    r = compute_priority(**full(buyer_intent=1.7, prompt_demand=-0.2))
    assert r.breakdown["buyer_intent"].value == 1.0
    assert r.breakdown["prompt_demand"].value == 0.0 and r.score == 0.0
    with pytest.raises(ValueError):
        compute_priority(**full(buyer_intent=math.nan))
    with pytest.raises(ValueError):
        compute_priority(**full(buyer_intent=math.inf))


def test_missing_components_use_flagged_defaults():
    kw = full()
    kw["persona_importance"] = None
    kw["competitive_displacement"] = None
    r = compute_priority(**kw)
    assert r.unknown_components == ["persona_importance", "competitive_displacement"]
    assert r.breakdown["persona_importance"].source == "default"
    assert r.breakdown["persona_importance"].value == 0.5
    assert r.breakdown["competitive_displacement"].value == 0.3


def test_weights_are_configurable_and_normalised():
    cfg = PriorityConfig(weights={**dict.fromkeys(COMPONENTS, 1.0), "buyer_intent": 3.0})
    heavy = compute_priority(**full(buyer_intent=0.2), config=cfg)
    even = compute_priority(**full(buyer_intent=0.2))
    assert heavy.score < even.score
    assert sum(c.weight for c in heavy.breakdown.values()) == pytest.approx(1.0)
    with pytest.raises(ValueError):
        compute_priority(**full(), config=PriorityConfig(weights=dict.fromkeys(COMPONENTS, 0.0)))


def test_to_dict_is_json_safe_and_complete():
    import json

    d = compute_priority(**full(buyer_intent=None), notes={"buyer_intent": "x"}).to_dict()
    json.dumps(d)
    assert d["components"]["buyer_intent"]["display"] == 50.0
    assert d["components"]["prompt_demand"]["label"] == "Prompt demand"
    assert d["score"] > 0


@pytest.mark.parametrize(
    "score,sev",
    [(100, Severity.CRITICAL), (70, Severity.CRITICAL), (69.9, Severity.HIGH), (50, Severity.HIGH),
     (49.9, Severity.MEDIUM), (30, Severity.MEDIUM), (29.9, Severity.LOW), (0, Severity.LOW)],
)
def test_severity_buckets_default(score, sev):
    assert severity_from_priority(score) is sev


def test_severity_thresholds_configurable_and_validated():
    t = SeverityThresholds(critical=90, high=60, medium=20)
    assert severity_from_priority(75, t) is Severity.HIGH
    assert severity_from_priority(25, t) is Severity.MEDIUM
    with pytest.raises(ValueError):
        SeverityThresholds(critical=40, high=60, medium=20)


def test_below_action_threshold():
    assert is_below_action_threshold(10) and not is_below_action_threshold(40)
    assert not is_below_action_threshold(10, PriorityConfig(observe_below=5))


def test_normalisers():
    assert demand_from_volume(500) == pytest.approx(0.5)
    assert demand_from_volume(0) == 0 and demand_from_volume(None) is None
    assert demand_from_volume(1e9) < 1.0
    assert displacement_from_competitor_gain(15) == pytest.approx(0.5)
    assert displacement_from_competitor_gain(100) == 1.0 and displacement_from_competitor_gain(-5) == 0.0
    assert displacement_from_competitor_gain(None) is None
    assert feasibility_for_category(IncidentCategory.STALE_INFORMATION) == 0.9
    assert feasibility_for_category("nonexistent") is None


def test_buyer_intent_heuristic():
    hi = buyer_intent_from_prompts(["best CRM pricing", "Acme vs Beta comparison"])
    lo = buyer_intent_from_prompts(["what is the history of CRM"])
    assert hi > 0.8 and lo < 0.3
    assert buyer_intent_from_prompts([]) is None and buyer_intent_from_prompts(None) is None


def test_persona_importance_lookup():
    personas = [{"name": "CISO", "importance": 0.9}, {"name": "Dev", "weight": 40}, "Marketer", {"name": "x"}]
    assert persona_importance_from_config(personas, "ciso") == pytest.approx(0.9)
    assert persona_importance_from_config(personas, "Dev") == pytest.approx(0.4)
    assert persona_importance_from_config(personas, "Marketer") is None
    assert persona_importance_from_config(personas) is None  # never guessed
    assert persona_importance_from_config([], "CISO") is None
