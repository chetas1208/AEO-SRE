"""Policy tests: features, cold start, convergence on a synthetic linear bandit, versions, propensities.
All randomness is seeded; the synthetic bandit is a test fixture, not AEO data."""
from __future__ import annotations

import numpy as np
import pytest
from app.domain.enums import ActionType, IncidentCategory, SelectionBasis
from app.models.policy import ImmutableVersionError, PolicyDecision, PolicyVersion
from app.policy import (
    ACTIONS,
    COLD_START_PRIORS,
    DIM,
    FEATURE_NAMES,
    UI_FEATURE_NAMES,
    LinUCBPolicy,
    PolicyConfig,
    PolicyStore,
    ThompsonPolicy,
    encode_context,
    next_version,
    prior_scores,
    resolve_allowed_actions,
)
from app.policy.features import coerce_context

A = ActionType
POLICIES = [LinUCBPolicy, ThompsonPolicy]


def ctx(**kw):
    return encode_context(None, **kw)


# ---------------------------------------------------------------- features
def test_feature_vector_fixed_order_and_names():
    required = ["visibility_delta", "citation_delta", "accuracy_delta", "competitor_delta", "prompt_volume",
                "buyer_intent", "source_authority", "source_freshness", "owned_source", "third_party_source",
                "content_exists", "factual_conflict", "persona_value", "action_cost", "historical_success"]
    assert list(FEATURE_NAMES[: len(required)]) == required
    assert [f"incident_type={c.value}" for c in IncidentCategory] == list(
        FEATURE_NAMES[len(required) : len(required) + len(IncidentCategory)])
    assert "bias" not in UI_FEATURE_NAMES and len(FEATURE_NAMES) == DIM
    x = ctx(incident_type="visibility_drop")
    assert x.shape == (DIM,) and x[FEATURE_NAMES.index("bias")] == 1.0
    assert x[FEATURE_NAMES.index("incident_type=visibility_drop")] == 1.0


def test_normalization_and_missing_not_invented():
    cv = encode_context(None, visibility_delta=-5.0, prompt_volume=1e9, source_age_days=0,
                        owned_source=True, return_details=True)
    d = cv.to_dict()
    assert d["visibility_delta"] == -1.0 and d["prompt_volume"] == 1.0 and d["source_freshness"] == 1.0
    assert d["owned_source"] == 1.0
    assert d["citation_delta"] == 0.0 and "citation_delta" in cv.missing  # unknown -> neutral, flagged
    assert d["historical_success"] == 0.5
    assert 0 < ctx(prompt_volume=100)[FEATURE_NAMES.index("prompt_volume")] < 1


def test_encode_from_incident_dict_metrics():
    inc = {"category": "visibility_drop", "metrics": [
        {"metric": "visibility_score", "value": 30.0, "baseline": 40.0},
        {"metric": "citation_share", "delta_pct": -20},
    ], "context": {"owned_source": 1, "content_exists": 1}}
    d = encode_context(inc, return_details=True).to_dict()
    assert d["visibility_delta"] == pytest.approx(-0.25) and d["citation_delta"] == pytest.approx(-0.2)
    assert d["owned_source"] == 1.0 and d["incident_type=visibility_drop"] == 1.0


def test_coerce_roundtrip_json():
    x = ctx(visibility_delta=-0.3, incident_type="factual_conflict")
    cv = encode_context(None, visibility_delta=-0.3, incident_type="factual_conflict", return_details=True)
    assert np.allclose(coerce_context(cv.to_json()), x)
    assert np.allclose(coerce_context(cv.to_dict()), x)
    with pytest.raises(ValueError):
        coerce_context([1.0, 2.0])


# ---------------------------------------------------------------- cold start priors
CASES = {
    "owned_outdated_page": (dict(owned_source=1, content_exists=1, source_age_days=700, prompt_volume=2000,
                                 visibility_delta=-0.3), A.UPDATE_EXISTING_PAGE),
    "third_party_misinformation": (dict(third_party_source=1, factual_conflict=0.9, content_exists=1,
                                        prompt_volume=2000, visibility_delta=-0.3), A.PUBLISHER_OUTREACH),
    "missing_canonical_info": (dict(content_exists=0, prompt_volume=2000, visibility_delta=-0.3),
                               A.CREATE_CANONICAL_PAGE),
    "minor_low_volume_fluctuation": (dict(prompt_volume=20, visibility_delta=-0.05), A.OBSERVE),
}


@pytest.mark.parametrize("name", CASES)
def test_cold_start_priors_pick_expected_action(name):
    kw, expected = CASES[name]
    x = ctx(incident_type="visibility_drop", **kw)
    pri, matched = prior_scores(x)
    assert name in matched
    assert max(pri, key=pri.get) == expected
    p = LinUCBPolicy(PolicyConfig(seed=0))
    top = max(p.score(x), key=lambda s: s.mean)
    assert top.action == expected  # prior dominates when n=0


def test_priors_are_readable_config():
    assert all(r.name and r.description and r.when and r.scores for r in COLD_START_PRIORS)
    assert {"owned_outdated_page", "third_party_misinformation", "missing_canonical_info",
            "minor_low_volume_fluctuation"} <= {r.name for r in COLD_START_PRIORS}


@pytest.mark.parametrize("cls", POLICIES)
def test_cold_start_labeling_then_learned(cls):
    p = cls(PolicyConfig(seed=3, min_related=5))
    x = ctx(incident_type="stale_information", owned_source=1, content_exists=1, source_age_days=900,
            prompt_volume=500)
    d = p.select(x)
    assert d.cold_start and d.selection_basis == SelectionBasis.COLD_START_PRIOR and d.n_related == 0
    for _ in range(4):
        p.update(x, A.UPDATE_EXISTING_PAGE, 0.4)
    assert p.select(x).selection_basis == SelectionBasis.COLD_START_PRIOR  # 4 < 5
    p.update(x, A.UPDATE_EXISTING_PAGE, 0.4)
    d = p.select(x)
    assert d.selection_basis == SelectionBasis.LEARNED_POLICY and not d.cold_start and d.n_related == 5
    other = ctx(incident_type="visibility_drop")  # a different incident type is still cold
    assert p.select(other).selection_basis == SelectionBasis.COLD_START_PRIOR


# ---------------------------------------------------------------- action mask, observe
def test_observe_always_legal_and_mask_respected():
    assert A.OBSERVE in resolve_allowed_actions([A.CREATE_FAQ])
    assert resolve_allowed_actions({"create_faq": True, "observe": False, "publisher_outreach": False}) == (
        A.OBSERVE, A.CREATE_FAQ)
    assert resolve_allowed_actions(["bogus_action"]) == (A.OBSERVE,)
    p = LinUCBPolicy(PolicyConfig(seed=1, allowed_actions=("create_faq",)))
    x = ctx(incident_type="visibility_drop", third_party_source=1, factual_conflict=1)
    d = p.select(x)
    assert set(d.allowed_actions) == {A.OBSERVE, A.CREATE_FAQ}
    assert {s.action for s in d.scores} == {A.OBSERVE, A.CREATE_FAQ}
    for _ in range(200):
        assert p.select(x).action in (A.OBSERVE, A.CREATE_FAQ)


@pytest.mark.parametrize("cls", POLICIES)
def test_observe_has_positive_probability(cls):
    p = cls(PolicyConfig(seed=0))
    probs = p.probabilities(ctx(incident_type="visibility_drop", owned_source=1, content_exists=1,
                                source_age_days=900, prompt_volume=5000))
    assert probs[A.OBSERVE] > 0


# ---------------------------------------------------------------- propensities / determinism
@pytest.mark.parametrize("cls", POLICIES)
def test_probabilities_valid_and_match_decision(cls):
    p = cls(PolicyConfig(seed=5, epsilon=0.1))
    x = ctx(incident_type="competitor_citation_gain", competitor_delta=0.5, visibility_delta=-0.3,
            prompt_volume=3000)
    probs = p.probabilities(x)
    assert sum(probs.values()) == pytest.approx(1.0)
    assert min(probs.values()) >= 0.1 / len(ACTIONS) - 1e-12  # epsilon floor => IPS support
    d = p.select(x)
    assert d.probability == pytest.approx(probs[d.action])
    assert sum(s.probability for s in d.scores) == pytest.approx(1.0)


@pytest.mark.parametrize("cls", POLICIES)
def test_empirical_frequency_matches_propensity(cls):
    p = cls(PolicyConfig(seed=11, epsilon=0.2))
    x = ctx(incident_type="visibility_drop", owned_source=1, content_exists=1, source_age_days=900,
            prompt_volume=3000)
    probs = p.probabilities(x)
    counts = {a: 0 for a in probs}
    n = 4000
    for _ in range(n):
        counts[p.select(x).action] += 1
    for a, pr in probs.items():
        assert counts[a] / n == pytest.approx(pr, abs=0.03)


@pytest.mark.parametrize("cls", POLICIES)
def test_seeded_determinism(cls):
    x = ctx(incident_type="visibility_drop", prompt_volume=100)
    a = [cls(PolicyConfig(seed=42)).select(x).action for _ in range(1)]
    p1, p2 = cls(PolicyConfig(seed=42)), cls(PolicyConfig(seed=42))
    s1 = [p1.select(x).action for _ in range(30)]
    s2 = [p2.select(x).action for _ in range(30)]
    assert s1 == s2 and a[0] == s1[0]


def test_rule_fallback_on_policy_error(monkeypatch):
    p = LinUCBPolicy(PolicyConfig(seed=0))
    x = ctx(incident_type="visibility_drop", third_party_source=1, factual_conflict=1, prompt_volume=2000)
    monkeypatch.setattr(p, "score", lambda c: (_ for _ in ()).throw(RuntimeError("boom")))
    d = p.select(x)
    assert d.selection_basis == SelectionBasis.RULE_FALLBACK and d.action == A.PUBLISHER_OUTREACH
    assert "boom" in d.note
    d2 = p.select([1.0, 2.0])  # garbage context -> still a legal action
    assert d2.action == A.OBSERVE and d2.selection_basis == SelectionBasis.RULE_FALLBACK


# ---------------------------------------------------------------- learning
def _synthetic(seed: int, n_ctx_dims=DIM):
    rng = np.random.default_rng(seed)
    theta = {a: rng.normal(0, 0.4, DIM) for a in ACTIONS}
    return rng, theta


def _draw_ctx(rng):
    x = np.zeros(DIM)
    n_scalar = len(FEATURE_NAMES) - len(IncidentCategory) - 1
    x[:n_scalar] = rng.uniform(0, 1, n_scalar)
    x[n_scalar + rng.integers(len(IncidentCategory))] = 1.0
    x[-1] = 1.0
    return x


def _greedy_regret(p, theta, rng, n=500):
    reg, hits = [], 0
    for _ in range(n):
        x = _draw_ctx(rng)
        means = {a: float(theta[a] @ x) for a in ACTIONS}
        pick = max(p.score(x), key=lambda s: s.mean).action
        reg.append(max(means.values()) - means[pick])
        hits += means[pick] == max(means.values())
    return float(np.mean(reg)), hits / n


@pytest.mark.parametrize("cls", POLICIES)
def test_convergence_on_synthetic_linear_bandit(cls):
    rng, theta = _synthetic(123)
    eps = 0.1
    cfg = PolicyConfig(seed=7, min_related=0, prior_strength=1.0, alpha=0.3, noise_std=0.1,
                       epsilon=eps, temperature=0.05)
    p = cls(cfg)
    before, _ = _greedy_regret(p, theta, np.random.default_rng(1))
    T = 4000
    online = []
    for _ in range(T):
        x = _draw_ctx(rng)
        means = {a: float(theta[a] @ x) for a in ACTIONS}
        d = p.select(x)
        p.update(x, d.action, means[d.action] + rng.normal(0, 0.05))
        online.append(max(means.values()) - means[d.action])
    after, acc = _greedy_regret(p, theta, np.random.default_rng(1))
    assert after < 0.1 * before and acc > 0.85
    # online regret is dominated by the epsilon floor: late regret ~ eps * (uniform regret), early is larger
    uniform_regret = before  # untrained policy ~ arbitrary action
    assert np.mean(online[-500:]) < np.mean(online[:500])
    assert np.mean(online[-500:]) < eps * 2.0 * max(uniform_regret, 1.0)
    arm = max(ACTIONS, key=lambda a: p.state.n[a])
    th, _ = p.state.posterior(arm)
    assert np.corrcoef(th, theta[arm])[0, 1] > 0.9


def test_update_rejects_bad_reward_and_actions():
    p = LinUCBPolicy()
    x = ctx(incident_type="visibility_drop")
    with pytest.raises(ValueError):
        p.update(x, A.OBSERVE, float("nan"))
    with pytest.raises(ValueError):
        p.update(x, "invent_new_action", 0.1)


@pytest.mark.parametrize("cls", POLICIES)
def test_state_serialization_roundtrip(cls):
    p = cls(PolicyConfig(seed=2))
    x = ctx(incident_type="visibility_drop", owned_source=1)
    p.update(x, A.CREATE_FAQ, 0.3)
    p.update(x, A.OBSERVE, -0.1)
    q = cls.from_state(p.to_state(), version="v0.0.9")
    assert [s.mean for s in p.score(x)] == pytest.approx([s.mean for s in q.score(x)])
    assert q.n_updates == 2 and q.version == "v0.0.9"


# ---------------------------------------------------------------- store: immutable versions
def test_next_version():
    assert next_version(None) == "v0.0.1" and next_version("v0.0.1") == "v0.0.2"
    assert next_version("v0.0.9") == "v0.0.10"


async def test_versions_immutable_and_chained(session):
    store = PolicyStore(session, "linucb", PolicyConfig(seed=1))
    v1 = await store.ensure_initial()
    assert v1.version == "v0.0.1" and v1.n_updates == 0 and v1.parent_id is None and v1.immutable
    assert v1.priors["rules"] and v1.algorithm == "linucb"
    snapshot = repr(v1.state)
    x = ctx(incident_type="visibility_drop", owned_source=1)
    v2 = await store.save_update(v1, x, A.UPDATE_EXISTING_PAGE, 0.5)
    v3 = await store.save_update(v2, x, A.UPDATE_EXISTING_PAGE, 0.2)
    assert (v2.version, v2.parent_id, v2.n_updates) == ("v0.0.2", v1.id, 1)
    assert (v3.version, v3.parent_id, v3.n_updates) == ("v0.0.3", v2.id, 2)
    assert repr(v1.state) == snapshot  # parent not mutated
    await session.commit()
    assert (await store.latest()).version == "v0.0.3"
    assert [v.version for v in await store.list_versions()] == ["v0.0.1", "v0.0.2", "v0.0.3"]

    v1_id = v1.id
    v1.n_updates = 99  # any in-place mutation is refused by the ORM
    with pytest.raises(ImmutableVersionError):
        await session.flush()
    await session.rollback()
    row = await session.get(PolicyVersion, v1_id)
    await session.delete(row)
    with pytest.raises(ImmutableVersionError):
        await session.flush()
    await session.rollback()
    assert (await store.get("v0.0.1")).n_updates == 0

    reloaded = await store.load_policy("v0.0.3")
    assert reloaded.n_updates == 2 and reloaded.version == "v0.0.3"


async def test_decide_logs_decision_with_propensity(session):
    store = PolicyStore(session, "thompson", PolicyConfig(seed=4))
    x = ctx(incident_type="visibility_drop", third_party_source=1, factual_conflict=1, prompt_volume=3000)
    decision, row = await store.decide(x)
    await session.commit()
    got = await session.get(PolicyDecision, row.id)
    assert got.selected_action == decision.action.value and got.probability == pytest.approx(decision.probability)
    assert got.selection_basis == "cold_start_prior" and got.cold_start is True
    assert len(got.scores) == len(ACTIONS) and got.context_vector["names"] == list(FEATURE_NAMES)
    v = await store.get(got.policy_version_id)
    assert v.version == "v0.0.1"


@pytest.mark.parametrize("algo", ["linucb", "thompson"])
async def test_decision_reproducible_from_persisted_context_and_version(session, algo):
    """V2 invariant 6: scores + selection probabilities are a pure function of (persisted version, persisted context,
    persisted allowed-actions mask). Recompute them from the DB rows only and compare with what was logged."""
    store = PolicyStore(session, algo, PolicyConfig(seed=11))
    v1 = await store.ensure_initial()
    x = ctx(incident_type="visibility_drop", owned_source=1, factual_conflict=1)
    v2 = await store.save_update(v1, x, A.UPDATE_EXISTING_PAGE, 0.6)  # a non-trivial learned state
    policy = await store.load_policy(v2)
    decision, row = await store.decide(x, policy=policy)
    await session.commit()

    got = await session.get(PolicyDecision, row.id)
    assert got.created_at is not None and got.policy_version_id == v2.id
    assert got.context_vector["schema"] and got.allowed_actions and got.scores
    ver = await session.get(PolicyVersion, got.policy_version_id)
    replay = await PolicyStore(session, algo).load_policy(ver)
    replay.config.allowed_actions = tuple(got.allowed_actions)
    ctx_back = np.array(got.context_vector["values"])
    probs = replay.probabilities(ctx_back)
    for s in got.scores:
        assert probs[A(s["action"])] == pytest.approx(s["probability"], abs=1e-9)
        rs = next(r for r in replay.score(ctx_back) if r.action.value == s["action"])
        assert rs.mean == pytest.approx(s["mean"], abs=1e-9) and rs.ucb == pytest.approx(s["ucb"], abs=1e-9)
    assert probs[A(got.selected_action)] == pytest.approx(got.probability, abs=1e-9)
    assert A.OBSERVE.value in got.allowed_actions
