"""Validate LinUCB / Thompson on a SYNTHETIC linear simulator (no external data, numpy only).

This is a sanity check of the implementation (the shared ridge posterior in `app.policy.bandit.LinearState`
and the full `LinUCBPolicy` / `ThompsonPolicy` classes). It is NOT used to train the AEO policy and says
nothing about AEO outcomes. It is the fallback when Open Bandit Pipeline cannot be installed
(see `obp_validation.py`).

    cd backend && .venv/bin/python -m app.learning.synthetic_validation
"""
from __future__ import annotations

import json
import sys
from typing import Any

import numpy as np

from app.domain.enums import IncidentCategory
from app.policy.bandit import ACTIONS, LinearState, LinUCBPolicy, PolicyConfig, ThompsonPolicy
from app.policy.features import DIM, FEATURE_NAMES


def run_generic(algo: str, n_actions: int = 10, dim: int = 8, T: int = 4000, seed: int = 0,
                scale: float = 0.5, ridge: float = 1.0, noise: float = 0.1) -> dict[str, Any]:
    """Online run on a linear world; `algo` in {linucb, thompson, random}. Returns regret summary."""
    world = np.random.default_rng(seed)
    theta = world.normal(0, 1, (n_actions, dim)) / np.sqrt(dim)
    rng = np.random.default_rng(seed + 1)
    state = LinearState(range(n_actions), dim, ridge)
    regret = np.zeros(T)
    for t in range(T):
        x = world.normal(0, 1, dim)
        means = theta @ x
        if algo == "random":
            a = int(rng.integers(n_actions))
        else:
            scores = []
            for k in range(n_actions):
                m, sd = state.predict(k, x)
                scores.append(m + scale * sd * (1.0 if algo == "linucb" else rng.standard_normal()))
            a = int(np.argmax(scores))
        state.update(a, x, float(means[a] + world.normal(0, noise)), "x")
        regret[t] = means.max() - means[a]
    cum = np.cumsum(regret)
    k = T // 10
    return {"algo": algo, "T": T, "mean_regret_first_10pct": float(regret[:k].mean()),
            "mean_regret_last_10pct": float(regret[-k:].mean()), "cumulative_regret": float(cum[-1])}


def run_full_policies(T: int = 3000, seed: int = 0) -> list[dict[str, Any]]:
    """The real policy classes (with cold-start prior blending) on a linear world over the AEO feature space."""
    out = []
    for cls in (LinUCBPolicy, ThompsonPolicy):
        world = np.random.default_rng(seed)
        theta = {a: world.normal(0, 0.4, DIM) for a in ACTIONS}
        p = cls(PolicyConfig(seed=seed, min_related=0, prior_strength=1.0, alpha=0.3, noise_std=0.1,
                             epsilon=0.1, temperature=0.05))
        n_scalar = len(FEATURE_NAMES) - len(IncidentCategory) - 1

        def draw(world=world, n_scalar=n_scalar):
            x = np.zeros(DIM)
            x[:n_scalar] = world.uniform(0, 1, n_scalar)
            x[n_scalar + world.integers(len(IncidentCategory))] = 1.0
            x[-1] = 1.0
            return x

        def greedy_regret(n=400, n_scalar=n_scalar, theta=theta, policy=p):
            w2 = np.random.default_rng(99)
            r = []
            for _ in range(n):
                x = np.zeros(DIM)
                x[:n_scalar] = w2.uniform(0, 1, n_scalar)
                x[n_scalar + w2.integers(len(IncidentCategory))] = 1.0
                x[-1] = 1.0
                m = {a: float(theta[a] @ x) for a in ACTIONS}
                pick = max(policy.score(x), key=lambda s: s.mean).action
                r.append(max(m.values()) - m[pick])
            return float(np.mean(r))

        before = greedy_regret()
        for _ in range(T):
            x = draw()
            d = p.select(x)
            p.update(x, d.action, float(theta[d.action] @ x) + world.normal(0, 0.05))
        out.append({"policy": cls.algorithm, "greedy_regret_before": before, "greedy_regret_after": greedy_regret(),
                    "T": T})
    return out


def main() -> int:
    res: dict[str, Any] = {"source": "synthetic_simulator", "note": "validation only; not used to train the AEO policy",
                           "generic": [run_generic(a) for a in ("random", "linucb", "thompson")],
                           "full_policies": run_full_policies()}
    g = {r["algo"]: r for r in res["generic"]}
    ok = all(g[a]["cumulative_regret"] < 0.3 * g["random"]["cumulative_regret"] for a in ("linucb", "thompson"))
    ok &= all(r["greedy_regret_after"] < 0.2 * r["greedy_regret_before"] for r in res["full_policies"])
    res["passed"] = bool(ok)
    print(json.dumps(res, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
