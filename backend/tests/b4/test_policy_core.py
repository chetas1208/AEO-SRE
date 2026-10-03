"""Policy core: feature schema, eligibility mask, schema upgrade, controlled learning evaluation (B4 tasks 1-4, 11)."""
import numpy as np
import pytest
from app.domain.enums import ActionType
from app.policy.bandit import ACTIONS, LinearState, LinUCBPolicy, PolicyConfig, ThompsonPolicy
from app.policy.features import (
    FEATURE_NAMES,
    FEATURE_NAMES_V1,
    FEATURE_SCHEMAS,
    SCHEMA,
    SCHEMA_DIMS,
    coerce_context,
    encode_context,
)
from app.policy.mask import EligibilityFacts, compute_action_mask
from app.policy.priors import COLD_START_PRIORS
from app.policy.store import prior_config_hash, version_meta

A = ActionType


def test_feature_schema_is_versioned_and_v1_prefix_is_stable():
    assert SCHEMA == "ctx-v2" and SCHEMA_DIMS["ctx-v1"] == 23 and SCHEMA_DIMS[SCHEMA] == len(FEATURE_NAMES)
    assert FEATURE_SCHEMAS["ctx-v1"] == FEATURE_NAMES[:23] == FEATURE_NAMES_V1
    v = encode_context(None, incident_type="visibility_drop", severity=1.0, evidence_confidence=0.8,
                       root_cause_layer="owned_content", memory_similar_n=3, return_details=True)
    j = v.to_json()
    assert j["schema"] == SCHEMA and len(j["values"]) == len(FEATURE_NAMES)
    assert v.values[FEATURE_NAMES.index("root_cause=owned_content")] == 1.0
    assert v.values[FEATURE_NAMES.index("severity")] == 1.0
    assert "evidence_confidence" not in v.missing and "persona_value" in v.missing  # missing is reported, not invented


def test_legacy_v1_vector_is_padded_by_name_with_neutrals():
    x1 = np.zeros(23)
    x2 = coerce_context(x1)
    assert x2.shape[0] == len(FEATURE_NAMES) and x2[FEATURE_NAMES.index("memory_uncertainty")] == 1.0
    assert coerce_context(x2, "ctx-v1").shape[0] == 23


@pytest.mark.parametrize("facts,masked", [
    (EligibilityFacts(), {A.UPDATE_EXISTING_PAGE, A.PUBLISHER_OUTREACH, A.CREATE_CANONICAL_PAGE}),
    (EligibilityFacts(evidence_collected=True), {A.UPDATE_EXISTING_PAGE, A.PUBLISHER_OUTREACH}),
    (EligibilityFacts(True, False, True), {A.PUBLISHER_OUTREACH, A.CREATE_CANONICAL_PAGE}),
    (EligibilityFacts(True, True, True), {A.CREATE_CANONICAL_PAGE}),
])
def test_eligibility_rules(facts, masked):
    m = compute_action_mask(facts)
    assert {A(a) for a in m.masked} == masked and A.OBSERVE in m.eligible
    assert set(m.eligible) | {A(a) for a in m.masked} == set(A)  # taxonomy unchanged: nothing added or dropped
    assert all(reason for reason in m.masked.values())


@pytest.mark.parametrize("cls", [LinUCBPolicy, ThompsonPolicy])
def test_masked_actions_are_never_scored_or_selected(cls):
    # context that makes the cold-start prior LOVE publisher outreach; it is masked, so it must never appear
    x = encode_context(None, incident_type="factual_conflict", third_party_source=1, factual_conflict=1)
    eligible = [A.OBSERVE, A.CREATE_FAQ]
    seen = set()
    for seed in range(300):
        p = cls(PolicyConfig(seed=seed))
        d = p.select(x, eligible=eligible, masked={"publisher_outreach": "no third-party source"})
        seen.add(d.action)
        assert {s.action for s in d.scores} == set(eligible)
        assert d.masked_actions["publisher_outreach"] == "no third-party source"
        assert A.PUBLISHER_OUTREACH not in d.allowed_actions
    assert seen <= set(eligible)
    assert A.PUBLISHER_OUTREACH not in p.probabilities(x, eligible)
    assert p.propensity(x, A.PUBLISHER_OUTREACH, eligible) == 0.0


def test_observe_can_never_be_masked_even_by_a_bad_mask():
    p = LinUCBPolicy(PolicyConfig(seed=1))
    d = p.select(encode_context(None), eligible=[A.CREATE_FAQ])
    assert A.OBSERVE in d.allowed_actions


def test_rule_fallback_also_respects_the_mask():
    x = encode_context(None, incident_type="factual_conflict", third_party_source=1, factual_conflict=1)
    d = LinUCBPolicy(PolicyConfig(seed=0)).rule_fallback(x, eligible=[A.OBSERVE, A.CREATE_FAQ])
    assert d.action in (A.OBSERVE, A.CREATE_FAQ)


def test_selection_is_replayable_from_the_recorded_uniform_draw():
    x = encode_context(None, incident_type="visibility_drop", content_exists=1, owned_source=1, source_age_days=500)
    p = ThompsonPolicy(PolicyConfig(seed=5))
    for _ in range(25):
        d = p.select(x)
        again = p.select(x, uniform_draw=d.uniform_draw)
        assert again.action == d.action and again.probability == d.probability


def test_old_state_upgrades_by_padding_and_keeps_old_feature_predictions():
    p1 = LinUCBPolicy(PolicyConfig(seed=0), LinearState(ACTIONS, 23, 1.0))
    assert p1.feature_schema == "ctx-v1"
    x1 = encode_context(None, incident_type="visibility_drop", visibility_delta=-0.4)[:23]
    for _ in range(6):
        p1.update(x1, A.CREATE_FAQ, 0.4)
    before = {s.action: s.mean for s in p1.score(x1)}
    p2 = p1.copy()
    assert p2.upgrade_schema() and p2.feature_schema == SCHEMA and p2.state.dim == len(FEATURE_NAMES)
    # same context, new-feature neutrals: the learned mean on the old features is unchanged
    x2 = coerce_context(x1)
    after = {s.action: s.mean for s in p2.score(x2)}
    assert all(abs(before[a] - after[a]) < 0.2 for a in before)
    assert p1.state.dim == 23  # the parent is never mutated


def test_cold_start_is_honest_and_prior_hash_is_content_addressed():
    p = LinUCBPolicy(PolicyConfig(seed=0))
    meta = version_meta(p, None)
    assert meta["cold_start"] is True and meta["rewarded_experiments"] == 0 and meta["parent_version"] is None
    assert "learned nothing" in meta["note"] and meta["feature_schema"] == SCHEMA
    h1 = prior_config_hash(COLD_START_PRIORS, p.config.to_json())
    assert h1 == meta["prior_config_hash"]
    assert h1 != prior_config_hash(COLD_START_PRIORS, {**p.config.to_json(), "alpha": 9.0})


# ------------------------------------------------------ controlled learning evaluation (NOT live learning)
@pytest.mark.parametrize("cls", [LinUCBPolicy, ThompsonPolicy])
def test_controlled_evaluation_repeated_favorable_raises_unfavorable_lowers_preference(cls):
    """CONTROLLED EVALUATION with synthetic rewards on a fixed context. It checks the update direction of the bandit,
    it is not evidence that the live system has learned anything."""
    x = encode_context(None, incident_type="visibility_drop", content_exists=1, owned_source=1, visibility_delta=-0.3)
    base = cls(PolicyConfig(seed=0))
    fav, unf = base.copy(), base.copy()
    p0 = {s.action: s.mean for s in base.score(x)}
    for _ in range(12):
        fav.update(x, A.CREATE_FAQ, 0.6)
        unf.update(x, A.CREATE_FAQ, -0.6)
    pf = {s.action: s.mean for s in fav.score(x)}
    pu = {s.action: s.mean for s in unf.score(x)}
    assert pf[A.CREATE_FAQ] > p0[A.CREATE_FAQ] > pu[A.CREATE_FAQ]
    assert fav.probabilities(x)[A.CREATE_FAQ] > base.probabilities(x)[A.CREATE_FAQ] > unf.probabilities(x)[A.CREATE_FAQ]
    assert base.state.n[A.CREATE_FAQ] == 0  # the original policy object was never touched
