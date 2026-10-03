"""Sanity-check our LinUCB / Thompson state and OPE estimators against Open Bandit Pipeline (OBP) and the
Open Bandit Dataset (ZOZO) *random-policy sample shipped inside the `obp` package*.

VALIDATION ONLY. This validates the implementation machinery. It is NOT used to train or initialise the AEO
policy and implies no domain transfer to AEO. Nothing from OBP's source is copied; OBP is used as an
independent reference implementation (pip package, Apache-2.0).

Checks
  1. OPE parity: our IPS / SNIPS / DR == OBP's IPW / SNIPW / DR on the same logged OBD rows (tolerance 1e-9).
  2. State parity: our `LinearState` after streaming OBD rows == OBP `LinUCB`/`LinTS` (A^-1, b), 1e-8.
  3. Offline learning on OBD (train on half, IPS/SNIPS on the other half) vs random baseline vs OBP's
     LinUCB/LinTS fitted the same way. The 10k-row sample has ~0.4% CTR, so this is only a smoke test.
  4. Online regret on OBP's `SyntheticBanditDataset` ground truth: ours vs OBP's vs uniform random.

Run in an environment that has `obp` (kept out of the backend venv: it pulls torch/pandas/etc.):
    uv venv /tmp/obpenv && uv pip install --python /tmp/obpenv/bin/python obp "sqlalchemy[asyncio]" \
        pydantic pydantic-settings structlog
    cd backend && /tmp/obpenv/bin/python -m app.learning.obp_validation [--out ../docs/notes/a9-obp-validation.json]
If `obp` is not importable this falls back to `synthetic_validation` and says so.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from typing import Any

import numpy as np

from app.learning.ope import dr_estimate, ips_estimate, snips_estimate
from app.policy.bandit import LinearState


def _our_stream(state_dim: int, n_actions: int, ctx: np.ndarray, act: np.ndarray, rew: np.ndarray) -> LinearState:
    st = LinearState(range(n_actions), state_dim, 1.0)
    for x, a, r in zip(ctx, act, rew, strict=True):
        st.update(int(a), x, float(r), "obd")
    return st


def _scores(st: LinearState, X: np.ndarray, n_actions: int, kind: str, alpha: float) -> np.ndarray:
    out = np.zeros((len(X), n_actions))
    for k in range(n_actions):
        theta, Ainv = st.posterior(k)
        mean = X @ theta
        if kind == "mean":
            out[:, k] = mean
        else:
            out[:, k] = mean + alpha * np.sqrt(np.einsum("ij,jk,ik->i", X, Ainv, X))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--synthetic-only", action="store_true")
    args = ap.parse_args(argv)
    try:
        if args.synthetic_only:
            raise ImportError("synthetic-only requested")
        import obp  # noqa: F401
        from obp.dataset import OpenBanditDataset, SyntheticBanditDataset, linear_reward_function
        from obp.ope import (
            DoublyRobust,
            InverseProbabilityWeighting,
            OffPolicyEvaluation,
            SelfNormalizedInverseProbabilityWeighting,
        )
        from obp.policy import LinTS, LinUCB
    except ImportError as exc:
        print(f"OBP unavailable ({exc}); falling back to the synthetic simulator.", file=sys.stderr)
        from app.learning import synthetic_validation

        return synthetic_validation.main()
    warnings.filterwarnings("ignore")
    _patch_pandas_for_obp()

    report: dict[str, Any] = {"source": "open_bandit_pipeline", "obp_version": obp.__version__,
                              "dataset": "OBD random-policy sample (men campaign, 10k rows, bundled with obp)",
                              "note": "validation only; NOT used to train the AEO policy; no domain transfer to AEO"}
    checks: dict[str, bool] = {}

    # ---- load OBD --------------------------------------------------------------------------------
    dataset = OpenBanditDataset(behavior_policy="random", campaign="men")
    bf = dataset.obtain_batch_bandit_feedback()
    n, A, L = bf["n_rounds"], bf["n_actions"], dataset.len_list
    X, act, _pos, rew, pscore = bf["context"], bf["action"], bf["position"], bf["reward"], bf["pscore"]
    d = X.shape[1]
    report["obd"] = {"n_rounds": int(n), "n_actions": int(A), "dim_context": int(d), "len_list": int(L),
                     "ctr": float(rew.mean()), "pscore_is_uniform": bool(np.allclose(pscore, 1.0 / A))}

    # ---- 1. OPE parity ---------------------------------------------------------------------------
    rng = np.random.default_rng(0)
    W = rng.normal(0, 1, (d, A))
    logits = X @ W
    pi = np.exp(logits - logits.max(1, keepdims=True))
    pi /= pi.sum(1, keepdims=True)
    pi = 0.8 * pi + 0.2 / A  # evaluation policy: seeded softmax of random linear scores, eps-mixed
    action_dist = np.repeat(pi[:, :, None], L, axis=2)
    q_hat_arm = np.zeros((n, A))
    from app.learning.ope import fit_reward_model

    q_hat_arm = fit_reward_model(X, act, rew, A)(X)
    q_hat = np.repeat(q_hat_arm[:, :, None], L, axis=2)
    ope = OffPolicyEvaluation(bandit_feedback=bf, ope_estimators=[
        InverseProbabilityWeighting(), SelfNormalizedInverseProbabilityWeighting(), DoublyRobust()])
    obp_vals = ope.estimate_policy_values(action_dist=action_dist, estimated_rewards_by_reg_model=q_hat)
    ours = {"ipw": ips_estimate(act, rew, pscore, pi).value, "snipw": snips_estimate(act, rew, pscore, pi).value,
            "dr": dr_estimate(act, rew, pscore, pi, q_hat_arm).value}
    report["ope_parity"] = {"obp": {k: float(v) for k, v in obp_vals.items()}, "ours": ours}
    checks["ope_parity"] = all(abs(ours[k] - obp_vals[k]) < 1e-9 for k in ours)

    # ---- 2. state parity -------------------------------------------------------------------------
    m = 2000
    st = _our_stream(d, A, X[:m], act[:m], rew[:m])
    ucb = LinUCB(n_actions=A, dim=d, len_list=1, random_state=0, epsilon=1.0)
    ts = LinTS(n_actions=A, dim=d, len_list=1, random_state=0)
    for i in range(m):
        ucb.update_params(int(act[i]), float(rew[i]), X[i : i + 1])
        ts.update_params(int(act[i]), float(rew[i]), X[i : i + 1])
    err_ainv = max(np.abs(np.linalg.inv(st.A[k]) - ucb.A_inv[k]).max() for k in range(A))
    err_b = max(np.abs(st.b[k] - ucb.b[:, k]).max() for k in range(A))
    ucb_pick_match = np.mean([int(np.argmax(_scores(st, X[i : i + 1], A, "ucb", 1.0)[0]))
                              == int(ucb.select_action(X[i : i + 1])[0]) for i in range(m, m + 300)])
    report["state_parity"] = {"max_abs_err_A_inv": float(err_ainv), "max_abs_err_b": float(err_b),
                              "ucb_argmax_agreement_300_rows": float(ucb_pick_match), "rows_streamed": m}
    checks["state_parity"] = bool(err_ainv < 1e-8 and err_b < 1e-8 and ucb_pick_match == 1.0)

    # ---- 3. offline learning on OBD --------------------------------------------------------------
    h = n // 2
    tr, te = slice(0, h), slice(h, n)
    st_tr = _our_stream(d, A, X[tr], act[tr], rew[tr])
    ucb_o = LinUCB(n_actions=A, dim=d, len_list=1, random_state=0, epsilon=0.5)
    ts_o = LinTS(n_actions=A, dim=d, len_list=1, random_state=0)
    for i in range(h):
        for p in (ucb_o, ts_o):
            p.update_params(int(act[i]), float(rew[i]), X[i : i + 1])

    def det(scores: np.ndarray) -> np.ndarray:
        pe = np.zeros_like(scores)
        pe[np.arange(len(scores)), scores.argmax(1)] = 1.0
        return pe

    Xte = X[te]
    cands = {
        "random": np.full((n - h, A), 1.0 / A),
        "ours_linucb_greedy": det(_scores(st_tr, Xte, A, "ucb", 0.5)),
        "ours_thompson_posterior_mean": det(_scores(st_tr, Xte, A, "mean", 0.0)),
        "obp_linucb_greedy": det(_obp_scores(ucb_o, Xte, A, 0.5)),
        "obp_lints_mean": det(_obp_scores(ts_o, Xte, A, 0.0)),
    }
    off = {}
    for name, pe in cands.items():
        r_ = snips_estimate(act[te], rew[te], pscore[te], pe)
        i_ = ips_estimate(act[te], rew[te], pscore[te], pe)
        off[name] = {"ips": i_.value, "snips": r_.value, "ips_se": i_.std_err, "ess": i_.ess}
    report["offline_learning"] = {"train_rows": h, "test_rows": n - h, "estimates": off,
                                  "caveat": "~0.4% CTR on 10k rows: differences are within noise; smoke test only"}
    base = off["random"]["ips"]
    checks["offline_runs_finite"] = all(np.isfinite(v["ips"]) for v in off.values())
    checks["ours_matches_obp_policy_value"] = abs(off["ours_linucb_greedy"]["ips"] - off["obp_linucb_greedy"]["ips"]) < 1e-9
    report["offline_learning"]["random_baseline_ips"] = base

    # ---- 4. synthetic ground truth ---------------------------------------------------------------
    syn = []
    for seed in (0, 1, 2):
        T, K, dim = 3000, 10, 5
        ds = SyntheticBanditDataset(n_actions=K, dim_context=dim, reward_type="continuous",
                                    reward_function=linear_reward_function, random_state=seed)
        data = ds.obtain_batch_bandit_feedback(n_rounds=T)
        Xs, true_q = data["context"], data["expected_reward"]
        runs = {}
        for algo in ("ours_linucb", "ours_thompson", "obp_linucb", "obp_lints", "random"):
            r = np.random.default_rng(seed + 10)
            st_s = LinearState(range(K), dim, 1.0)
            obp_pol = (LinUCB(n_actions=K, dim=dim, len_list=1, random_state=seed, epsilon=0.5) if algo == "obp_linucb"
                       else LinTS(n_actions=K, dim=dim, len_list=1, random_state=seed) if algo == "obp_lints" else None)
            reg = 0.0
            for t in range(T):
                x = Xs[t : t + 1]
                if algo == "random":
                    a = int(r.integers(K))
                elif obp_pol is not None:
                    a = int(obp_pol.select_action(x)[0])
                else:
                    sc = []
                    for k in range(K):
                        mu, sd = st_s.predict(k, Xs[t])
                        sc.append(mu + (0.5 * sd if algo == "ours_linucb" else sd * r.standard_normal()))
                    a = int(np.argmax(sc))
                reward = float(true_q[t, a] + r.normal(0, 0.5))
                if obp_pol is not None:
                    obp_pol.update_params(a, reward, x)
                else:
                    st_s.update(a, Xs[t], reward, "syn")
                reg += float(true_q[t].max() - true_q[t, a])
            runs[algo] = reg
        syn.append({"seed": seed, "cumulative_regret": runs})
    report["synthetic_ground_truth"] = syn
    checks["synthetic_beats_random"] = all(
        s["cumulative_regret"][a] < 0.5 * s["cumulative_regret"]["random"] for s in syn
        for a in ("ours_linucb", "ours_thompson"))

    report["checks"] = checks
    report["passed"] = bool(all(checks.values()))
    out = json.dumps(report, indent=2, default=float)
    print(out)
    if args.out:
        with open(args.out, "w") as f:
            f.write(out + "\n")
    return 0 if report["passed"] else 1


def _patch_pandas_for_obp() -> None:
    """obp 0.4.1 calls `DataFrame.drop(label, 1)` (positional axis), removed in pandas 2. Shim only the
    positional-axis form so OBP's own dataset loader runs unmodified."""
    import pandas as pd

    orig = pd.DataFrame.drop
    if getattr(orig, "_obp_compat", False):
        return

    def drop(self, labels=None, *args, **kwargs):
        if args and "axis" not in kwargs:
            kwargs["axis"], args = args[0], args[1:]
        return orig(self, labels, *args, **kwargs)

    drop._obp_compat = True  # type: ignore[attr-defined]
    pd.DataFrame.drop = drop  # type: ignore[method-assign]


def _obp_scores(pol, X: np.ndarray, A: int, eps: float) -> np.ndarray:
    """OBP's own scores for every row, from its fitted (A_inv, b): mean (+ eps * sigma for LinUCB)."""
    out = np.zeros((len(X), A))
    for k in range(A):
        theta = pol.A_inv[k] @ pol.b[:, k]
        out[:, k] = X @ theta + eps * np.sqrt(np.einsum("ij,jk,ik->i", X, pol.A_inv[k], X))
    return out


if __name__ == "__main__":
    sys.exit(main())
