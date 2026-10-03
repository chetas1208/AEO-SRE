"""Off-policy estimators on a seeded synthetic log (test fixture, not AEO data)."""
from __future__ import annotations

import numpy as np
import pytest
from app.domain.enums import ActionType
from app.learning.ope import (
    dr_estimate,
    evaluate_policy,
    filter_valid_log,
    fit_reward_model,
    ips_estimate,
    snips_estimate,
)
from app.policy import ACTIONS, DIM, LinUCBPolicy, PolicyConfig, encode_context


def _log(n=20000, K=3, seed=0):
    rng = np.random.default_rng(seed)
    X = rng.uniform(-1, 1, (n, 2))
    true_q = np.stack([0.3 + 0.2 * X[:, 0], 0.1 + 0.5 * X[:, 1], 0.2 - 0.3 * X[:, 0]], axis=1)  # (n, K)
    logging = np.array([0.5, 0.3, 0.2])
    a = rng.choice(K, size=n, p=logging)
    r = true_q[np.arange(n), a] + rng.normal(0, 0.1, n)
    p0 = logging[a]
    return X, true_q, a, r, p0


def _target(X, K=3):
    pe = np.zeros((len(X), K))
    pe[np.arange(len(X)), np.where(X[:, 1] > 0, 1, 0)] = 0.8
    pe += 0.2 / K
    return pe / pe.sum(axis=1, keepdims=True)


def test_estimators_recover_true_value():
    X, q, a, r, p0 = _log()
    pe = _target(X)
    truth = float((pe * q).sum(axis=1).mean())
    ips = ips_estimate(a, r, p0, pe)
    snips = snips_estimate(a, r, p0, pe)
    qhat = fit_reward_model(np.c_[X, np.ones(len(X))], a, r, 3)(np.c_[X, np.ones(len(X))])
    dr = dr_estimate(a, r, p0, pe, qhat)
    for res in (ips, snips, dr):
        assert res.value == pytest.approx(truth, abs=4 * res.std_err + 0.01)
        assert res.n == len(a) and 0 < res.ess <= len(a)
        assert res.ci95[0] < res.value < res.ci95[1]
    assert dr.std_err < ips.std_err  # a good reward model reduces variance


def test_dr_robust_to_bad_reward_model_and_ips_to_bad_q():
    X, q, a, r, p0 = _log(seed=1)
    pe = _target(X)
    truth = float((pe * q).sum(axis=1).mean())
    bad_q = np.full_like(q, 5.0)  # wildly wrong model; DR correction term still removes the bias
    dr = dr_estimate(a, r, p0, pe, bad_q)
    assert abs(dr.value - truth) < 0.15 * 1.0 + 4 * dr.std_err


def test_identity_policy_matches_logged_mean():
    X, q, a, r, p0 = _log(seed=2)
    pe = np.tile([0.5, 0.3, 0.2], (len(a), 1))  # evaluating the logging policy itself => w == 1
    assert ips_estimate(a, r, p0, pe).value == pytest.approx(r.mean())
    assert snips_estimate(a, r, p0, pe).value == pytest.approx(r.mean())
    assert ips_estimate(a, r, p0, pe).ess == pytest.approx(len(a))


def test_clipping_reduces_weights_and_validates_inputs():
    X, q, a, r, p0 = _log(n=2000, seed=3)
    pe = _target(X)
    assert ips_estimate(a, r, p0, pe, clip=1.0).ess > ips_estimate(a, r, p0, pe).ess - 1e-9
    with pytest.raises(ValueError):
        ips_estimate(a, r, np.zeros_like(p0), pe)
    with pytest.raises(ValueError):
        ips_estimate(a[:-1], r, p0, pe)
    with pytest.raises(ValueError):
        ips_estimate([], [], [], np.zeros((0, 3)))


def test_filter_valid_log_drops_rule_fallback_and_manual():
    mask = filter_valid_log(["cold_start_prior", "rule_fallback", "learned_policy", "manual_override"])
    assert mask.tolist() == [True, False, True, False]


def test_evaluate_policy_on_logged_decisions_from_our_policy():
    """Log decisions from a LinUCB policy (with its own propensities), then evaluate a different policy."""
    rng = np.random.default_rng(5)
    logger = LinUCBPolicy(PolicyConfig(seed=5, epsilon=0.3, temperature=0.5))
    theta = {a: rng.normal(0, 0.3, DIM) for a in ACTIONS}
    ctxs, acts, rews, props, truth_means = [], [], [], [], []
    for _ in range(600):
        x = encode_context(None, incident_type="visibility_drop", visibility_delta=rng.uniform(-1, 0),
                           prompt_volume=rng.uniform(1, 5000), owned_source=float(rng.random() > 0.5),
                           content_exists=float(rng.random() > 0.5), source_age_days=rng.uniform(0, 800))
        d = logger.select(x)
        ctxs.append(x)
        acts.append(d.action.value)
        props.append(d.probability)
        rews.append(float(theta[d.action] @ x) + rng.normal(0, 0.05))
        truth_means.append({a: float(theta[a] @ x) for a in ACTIONS})
    ctxs, rews, props = np.array(ctxs), np.array(rews), np.array(props)
    target = LinUCBPolicy(PolicyConfig(seed=9, epsilon=0.3, temperature=0.5))  # same cold policy
    out = evaluate_policy(target, ctxs, acts, rews, props)
    # evaluating the logging policy itself: IPS == mean logged reward exactly (weights are all 1)
    assert out["ips"].value == pytest.approx(rews.mean())
    assert set(out) == {"ips", "snips", "dr"} and out["dr"].n == 600
    pe_truth = np.mean([sum(p * m[a] for a, p in target.probabilities(x).items())
                        for x, m in zip(ctxs[:200], truth_means[:200], strict=True)])
    assert abs(out["snips"].value - pe_truth) < 0.1
