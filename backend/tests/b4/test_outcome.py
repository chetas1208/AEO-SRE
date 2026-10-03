"""Outcome methodology (B4 task 5 + 9): labels, observe mapping, inconclusive handling, causal wording."""
from app.domain.enums import ActionType, Risk
from app.experiments.outcome import ObserveOutcome, Outcome, assess
from app.experiments.spec import build_spec, validate_spec

BEFORE = {"visibility": 0.40, "citation_share": 0.20, "accuracy": 0.90, "competitor_share": 0.50}


def _spec(action=ActionType.UPDATE_EXISTING_PAGE, primary="visibility"):
    from datetime import UTC, datetime, timedelta

    t = datetime(2026, 10, 1, tzinfo=UTC)
    s = build_spec(action=action, root_cause="owned page is stale", category="visibility_drop",
                   before_metrics=BEFORE, window_start=t + timedelta(hours=48), window_end=t + timedelta(days=9),
                   executed_after=timedelta(hours=48), declared_at=t).to_json()
    assert s["primary_metric"] == primary
    return s


def _a(after, action=ActionType.UPDATE_EXISTING_PAGE, **kw):
    return assess(before=BEFORE, after=after, action=action, risk=Risk.LOW, spec=_spec(action), **kw)


def test_spec_states_if_because_then_after_and_is_complete():
    s = _spec()
    assert s["statement"].startswith("IF we apply update_existing_page BECAUSE owned page is stale THEN visibility SHOULD increase AFTER")
    assert s["secondary_metrics"] == ["citation_share", "accuracy", "competitor_share"]
    validate_spec(s, before_metrics=BEFORE)


def test_primary_metric_must_have_been_measured():
    import pytest
    from app.experiments.spec import SpecError

    with pytest.raises(SpecError):
        validate_spec({**_spec(), "primary_metric": "accuracy"} | {"spec_hash": None}, before_metrics={"visibility": 0.4})


def test_favorable_unfavorable_neutral_from_declared_primary_metric():
    assert _a({**BEFORE, "visibility": 0.50}).outcome is Outcome.FAVORABLE
    assert _a({**BEFORE, "visibility": 0.30}).outcome is Outcome.UNFAVORABLE
    n = _a({**BEFORE, "visibility": 0.41})
    assert n.outcome is Outcome.NEUTRAL and n.learn and n.reward is not None


def test_inconclusive_has_no_reward_update_and_says_so():
    a = _a({"citation_share": 0.30})  # primary metric (visibility) not measured after
    assert a.outcome is Outcome.INCONCLUSIVE and not a.learn
    assert "No policy update" in a.causal_statement
    assert "visibility" not in a.components()  # the unmeasured primary metric is absent, informational components only


def test_components_persist_only_available_metrics_plus_cost_and_risk():
    a = _a({"visibility": 0.5, "citation_share": 0.3})
    assert set(a.components()) == {"visibility", "citation", "action_cost", "risk_penalty"}


def test_hard_confounder_makes_outcome_inconclusive_soft_one_lowers_confidence():
    hard = _a({**BEFORE, "visibility": 0.5}, confounders=[{"kind": "overlapping_intervention", "hard": True,
                                                            "detail": "x"}])
    assert hard.outcome is Outcome.INCONCLUSIVE and not hard.learn
    soft = _a({**BEFORE, "visibility": 0.5}, confounders=[{"kind": "competitor_change", "hard": False, "detail": "x"}])
    assert soft.outcome is Outcome.FAVORABLE and soft.causal_confidence == "low"


def test_never_claims_causality_or_high_confidence():
    for after in ({**BEFORE, "visibility": 0.6}, {**BEFORE, "visibility": 0.2}):
        a = _a(after)
        assert a.causal_confidence in ("low", "medium")
        assert "not a demonstrated cause" in a.causal_statement


def test_observe_mapping_self_recovery_persistent_worsened_inconclusive():
    obs = ActionType.OBSERVE
    assert _a({**BEFORE, "visibility": 0.48}, obs).observe_outcome is ObserveOutcome.SELF_RECOVERY
    assert _a({**BEFORE, "visibility": 0.40}, obs).observe_outcome is ObserveOutcome.PERSISTENT
    assert _a({**BEFORE, "visibility": 0.30}, obs).observe_outcome is ObserveOutcome.WORSENED
    inc = _a({"accuracy": 0.9}, obs)
    assert inc.observe_outcome is ObserveOutcome.INCONCLUSIVE and not inc.learn
    sp = _spec(obs)
    assert sp["observe"] is True and "no change is made" in sp["statement"]
    assert _a({**BEFORE, "visibility": 0.48}, obs).reward.components["action_cost"] == 0.0


def test_observe_with_intervention_during_window_is_inconclusive():
    a = _a({**BEFORE, "visibility": 0.48}, ActionType.OBSERVE,
           confounders=[{"kind": "intervention_during_observe", "hard": True, "detail": "x"}])
    assert a.outcome is Outcome.INCONCLUSIVE and a.observe_outcome is ObserveOutcome.INCONCLUSIVE


def test_nothing_comparable_at_all_is_not_an_outcome_it_keeps_waiting():
    import pytest
    from app.learning.reward import NoObservation

    with pytest.raises(NoObservation):
        _a({"unrelated_metric": 5.0})
