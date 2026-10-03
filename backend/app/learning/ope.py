"""Off-policy evaluation on logged decisions that carry propensities.

Estimates V(pi_e) = E[r under evaluation policy pi_e] from data logged by pi_0 with propensity
p0 = pi_0(a|x):

    IPS    = mean( w * r ),                    w = pi_e(a|x) / p0
    SNIPS  = sum(w * r) / sum(w)               (self-normalized; biased but lower variance)
    DR     = mean( sum_a pi_e(a|x) q(x,a) + w * (r - q(x,a_logged)) )   q = reward model

Requires p0 > 0 for every logged row (the policies mix an epsilon-uniform floor to guarantee it).
Rows with selection_basis `rule_fallback` / `manual_override` have no valid propensity and must be
excluded by the caller (`filter_valid_log`). With little AEO data these estimates are high variance;
`ess` (effective sample size) is reported so the UI can say "insufficient history".
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from app.domain.enums import SelectionBasis

INVALID_BASES = {SelectionBasis.RULE_FALLBACK.value, SelectionBasis.MANUAL_OVERRIDE.value}


@dataclass
class OPEResult:
    estimator: str
    value: float
    std_err: float
    n: int
    ess: float  # (sum w)^2 / sum w^2
    ci95: tuple[float, float]


def filter_valid_log(selection_bases: Sequence[str]) -> np.ndarray:
    return np.array([str(b) not in INVALID_BASES for b in selection_bases], dtype=bool)


def _weights(actions: np.ndarray, propensities: np.ndarray, pi_e: np.ndarray, clip: float | None) -> np.ndarray:
    p0 = np.asarray(propensities, dtype=float)
    if np.any(p0 <= 0) or np.any(~np.isfinite(p0)):
        raise ValueError("logged propensities must be positive and finite")
    w = pi_e[np.arange(len(actions)), actions] / p0
    return np.minimum(w, clip) if clip is not None else w


def _result(name: str, per_sample: np.ndarray, w: np.ndarray, value: float | None = None) -> OPEResult:
    n = len(per_sample)
    v = float(per_sample.mean()) if value is None else value
    se = float(per_sample.std(ddof=1) / np.sqrt(n)) if n > 1 else float("nan")
    ess = float(w.sum() ** 2 / max((w**2).sum(), 1e-12))
    return OPEResult(name, v, se, n, ess, (v - 1.96 * se, v + 1.96 * se))


def _prep(actions, rewards, propensities, pi_e):
    a = np.asarray(actions, dtype=int)
    r = np.asarray(rewards, dtype=float)
    pe = np.asarray(pi_e, dtype=float)
    if not (len(a) == len(r) == len(propensities) == len(pe)):
        raise ValueError("actions, rewards, propensities and pi_e must have the same length")
    if len(a) == 0:
        raise ValueError("empty log")
    return a, r, pe


def ips_estimate(actions, rewards, propensities, pi_e, clip: float | None = None) -> OPEResult:
    """Inverse propensity scoring. `pi_e`: (n, K) evaluation-policy action probabilities per logged row."""
    a, r, pe = _prep(actions, rewards, propensities, pi_e)
    w = _weights(a, np.asarray(propensities), pe, clip)
    return _result("ips", w * r, w)


def snips_estimate(actions, rewards, propensities, pi_e, clip: float | None = None) -> OPEResult:
    a, r, pe = _prep(actions, rewards, propensities, pi_e)
    w = _weights(a, np.asarray(propensities), pe, clip)
    value = float((w * r).sum() / max(w.sum(), 1e-12))
    # delta-method standard error for the ratio estimator
    se_samples = w * (r - value) / max(w.mean(), 1e-12)
    return _result("snips", se_samples, w, value=value)


def dr_estimate(actions, rewards, propensities, pi_e, q_hat, clip: float | None = None) -> OPEResult:
    """Doubly robust. `q_hat`: (n, K) predicted reward for every action at each logged context."""
    a, r, pe = _prep(actions, rewards, propensities, pi_e)
    q = np.asarray(q_hat, dtype=float)
    w = _weights(a, np.asarray(propensities), pe, clip)
    idx = np.arange(len(a))
    per = (pe * q).sum(axis=1) + w * (r - q[idx, a])
    return _result("dr", per, w)


def fit_reward_model(contexts: np.ndarray, actions: np.ndarray, rewards: np.ndarray, n_actions: int,
                     ridge: float = 1.0):
    """Per-action ridge regression q(x, a). Returns predict(contexts) -> (n, K)."""
    X = np.asarray(contexts, dtype=float)
    d = X.shape[1]
    thetas = np.zeros((n_actions, d))
    for k in range(n_actions):
        m = np.asarray(actions) == k
        if m.any():
            Xk = X[m]
            thetas[k] = np.linalg.solve(Xk.T @ Xk + ridge * np.eye(d), Xk.T @ np.asarray(rewards)[m])
    return lambda ctx: np.asarray(ctx, dtype=float) @ thetas.T


def evaluate_policy(policy, contexts: np.ndarray, actions: np.ndarray, rewards: np.ndarray,
                    propensities: np.ndarray, *, clip: float | None = None) -> dict[str, OPEResult]:
    """IPS / SNIPS / DR for an `app.policy` Policy on a log. Actions are ActionType values or indices
    into `ACTIONS`; the policy's probabilities are taken over its own allowed set (zero elsewhere)."""
    from app.domain.enums import ActionType
    from app.policy.bandit import ACTIONS

    idx = np.array([ACTIONS.index(ActionType(a)) if not isinstance(a, (int, np.integer)) else int(a)
                    for a in actions])
    pe = np.zeros((len(idx), len(ACTIONS)))
    for i, x in enumerate(contexts):
        for act, p in policy.probabilities(x).items():
            pe[i, ACTIONS.index(act)] = p
    q = fit_reward_model(contexts, idx, rewards, len(ACTIONS))(contexts)
    return {
        "ips": ips_estimate(idx, rewards, propensities, pe, clip),
        "snips": snips_estimate(idx, rewards, propensities, pe, clip),
        "dr": dr_estimate(idx, rewards, propensities, pe, q, clip),
    }


async def load_logged_feedback(session) -> dict[str, np.ndarray | list]:
    """Rewarded experiments as a log: contexts, actions (ActionType values), rewards, propensities.
    Only rows with a stored propensity and a measured Reward; invalid-basis rows are dropped."""
    from sqlalchemy import select

    from app.domain.enums import ActionType
    from app.models.interventions import Experiment, Intervention, Reward  # A5-owned
    from app.policy.features import coerce_context

    q = (select(Experiment, Intervention, Reward)
         .join(Intervention, Intervention.id == Experiment.intervention_id)
         .join(Reward, Reward.experiment_id == Experiment.id))
    ctxs, acts, rews, props = [], [], [], []
    for exp, iv, rw in (await session.execute(q)).all():
        basis = exp.selection_basis or iv.selection_basis
        if not exp.context_vector or not exp.policy_probability or str(getattr(basis, 'value', basis)) in INVALID_BASES:
            continue
        ctxs.append(coerce_context(exp.context_vector))
        acts.append(ActionType(exp.selected_action or iv.action).value)
        rews.append(float(rw.total))
        props.append(float(exp.policy_probability))
    return {"contexts": np.array(ctxs), "actions": acts, "rewards": np.array(rews), "propensities": np.array(props)}
