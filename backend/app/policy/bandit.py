"""Contextual bandits for intervention selection: disjoint LinUCB and Bayesian-linear Thompson sampling.

No deep RL. Each action has its own ridge-regression posterior over the reward given the context
(`A_a = lambda*I + sum x x^T`, `b_a = sum r x`, `theta_a = A_a^-1 b_a`).

Scoring (per allowed action a, context x)
    learned_mean_a  = theta_a . x
    learned_unc_a   = scale * sqrt(x^T A_a^-1 x)       scale = alpha (LinUCB) | noise_std (Thompson)
    w_a             = n_a / (n_a + prior_strength)      weight of learned state, n_a = observations of a
    mean_a          = w_a * learned_mean_a + (1 - w_a) * cold_start_prior_a(x)
    uncertainty_a   = w_a * learned_unc_a  + (1 - w_a) * prior_uncertainty
    ucb_a           = mean_a + uncertainty_a

Propensities (stored for off-policy evaluation; support is full over allowed actions)
    LinUCB:   pi(a|x) = (1-eps) * softmax(ucb / temperature)_a + eps / K
    Thompson: pi(a|x) = (1-eps) * P_draws[a = argmax sampled value] + eps / K, where the probability
              is computed from a FIXED seeded set of M standard-normal draws (common random numbers),
              so pi is a deterministic function of (state, x) and the logged propensity is exact for
              the sampling step that follows.
The action is then sampled from pi with a seeded RNG, so `probability` is the true selection propensity.

Selection basis: `cold_start_prior` until `min_related` verified experiments with the same incident type
have been observed, then `learned_policy`; `rule_fallback` if scoring raises (prior argmax, else observe).
`observe` is ALWAYS in the allowed set.
"""
from __future__ import annotations

import copy
from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

from app.domain.enums import ActionType, SelectionBasis
from app.policy.features import (
    DIM,
    FEATURE_SCHEMAS,
    LEGACY_SCHEMA,
    SCHEMA,
    SCHEMA_DIMS,
    coerce_context,
    incident_type_of,
)
from app.policy.priors import COLD_START_PRIORS, DEFAULT_PRIOR, PriorRule, prior_scores

ACTIONS: tuple[ActionType, ...] = tuple(ActionType)


def resolve_allowed_actions(
    configured: Iterable[str | ActionType] | Mapping[str, bool] | str | None = None,
) -> tuple[ActionType, ...]:
    """Allowed-actions mask. None -> read `settings.allowed_actions` if present, else all actions.
    `observe` is always included. Unknown names are ignored (the vocabulary is fixed)."""
    if configured is None:
        from app.core.config import get_settings

        configured = getattr(get_settings(), "allowed_actions", None)
    if configured is None or (not isinstance(configured, Mapping) and not configured):
        return ACTIONS
    if isinstance(configured, str):
        configured = [s.strip() for s in configured.split(",") if s.strip()]
    elif isinstance(configured, Mapping):  # {"update_existing_page": true, "publisher_outreach": false}
        configured = [k for k, v in configured.items() if v]
    wanted = set()
    for c in configured:
        try:
            wanted.add(ActionType(c))
        except ValueError:
            continue
    wanted.add(ActionType.OBSERVE)
    return tuple(a for a in ACTIONS if a in wanted)


@dataclass(frozen=True)
class ActionScore:
    action: ActionType
    mean: float
    uncertainty: float
    ucb: float
    prior: float = 0.0
    learned_mean: float = 0.0
    learned_weight: float = 0.0
    n_observations: int = 0
    probability: float = 0.0

    def to_json(self) -> dict[str, Any]:
        d = asdict(self)
        d["action"] = self.action.value
        return d


@dataclass
class Decision:
    action: ActionType
    probability: float
    scores: list[ActionScore]
    selection_basis: SelectionBasis
    cold_start: bool
    policy_version: str
    n_related: int = 0
    allowed_actions: tuple[ActionType, ...] = ACTIONS
    matched_rules: list[str] = field(default_factory=list)
    algorithm: str = ""
    context: np.ndarray | None = None
    note: str = ""
    feature_schema: str = SCHEMA
    masked_actions: dict[str, str] = field(default_factory=dict)  # action -> reason it was not selectable
    uniform_draw: float | None = None  # u in [0,1): inverse-CDF draw over `probabilities` (reproduces the selection)
    full_context: np.ndarray | None = None  # context in the CURRENT feature schema (what gets persisted / learned from)
    policy_decision_id: Any = None  # PolicyDecision.id once recorded

    def scores_json(self) -> list[dict[str, Any]]:
        return [s.to_json() for s in self.scores]


@dataclass
class PolicyConfig:
    alpha: float = 0.5  # LinUCB exploration width
    noise_std: float = 0.25  # Thompson posterior scale (reward noise)
    ridge: float = 1.0
    min_related: int = 5  # verified same-type experiments before basis flips to learned_policy
    prior_strength: float = 5.0  # pseudo-count of the cold-start prior in the blend
    prior_uncertainty: float = 0.15
    temperature: float = 0.1  # softmax temperature (LinUCB)
    epsilon: float = 0.05  # uniform mixture weight in the propensity
    thompson_draws: int = 512
    seed: int | None = None
    allowed_actions: tuple[str, ...] | dict[str, bool] | None = None

    def to_json(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> PolicyConfig:
        allowed = d.get("allowed_actions")
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        known["allowed_actions"] = allowed if isinstance(allowed, dict) else (tuple(allowed) if allowed else None)
        return cls(**known)


def _full_or_none(context: Any) -> np.ndarray | None:
    try:
        return coerce_context(context)
    except Exception:  # noqa: BLE001
        return None


class LinearState:
    """Disjoint per-action ridge statistics. JSON-serializable; never shared between versions."""

    def __init__(self, actions: Iterable[ActionType] = ACTIONS, dim: int = DIM, ridge: float = 1.0):
        self.dim = dim
        self.ridge = ridge
        self.actions = tuple(actions)
        self.A = {a: ridge * np.eye(dim) for a in self.actions}
        self.b = {a: np.zeros(dim) for a in self.actions}
        self.n = {a: 0 for a in self.actions}
        self.type_counts: dict[str, int] = {}
        self.n_updates = 0

    @property
    def schema(self) -> str:
        return next((k for k, d in SCHEMA_DIMS.items() if d == self.dim), f"dim-{self.dim}")

    def pad_to(self, dim: int) -> None:
        """Zero-pad to a larger ctx schema (A -> blockdiag(A, ridge I), b -> [b, 0]); old statistics stay exact."""
        if dim < self.dim:
            raise ValueError("cannot shrink a policy state")
        if dim == self.dim:
            return
        extra = dim - self.dim
        for a in self.actions:
            A = self.ridge * np.eye(dim)
            A[: self.dim, : self.dim] = self.A[a]
            self.A[a] = A
            self.b[a] = np.concatenate([self.b[a], np.zeros(extra)])
        self.dim = dim

    def posterior(self, a: ActionType) -> tuple[np.ndarray, np.ndarray]:
        Ainv = np.linalg.inv(self.A[a])
        return Ainv @ self.b[a], Ainv

    def predict(self, a: ActionType, x: np.ndarray) -> tuple[float, float]:
        """(posterior mean reward, sqrt(x^T A^-1 x))."""
        theta, Ainv = self.posterior(a)
        return float(theta @ x), float(np.sqrt(max(x @ Ainv @ x, 0.0)))

    def update(self, a: ActionType, x: np.ndarray, r: float, type_key: str) -> None:
        self.A[a] = self.A[a] + np.outer(x, x)
        self.b[a] = self.b[a] + r * x
        self.n[a] += 1
        self.type_counts[type_key] = self.type_counts.get(type_key, 0) + 1
        self.n_updates += 1

    def to_json(self) -> dict[str, Any]:
        return {
            "schema": "linear-v1", "feature_schema": self.schema, "dim": self.dim, "ridge": self.ridge,
            "actions": [a.value for a in self.actions],
            "A": {a.value: self.A[a].tolist() for a in self.actions},
            "b": {a.value: self.b[a].tolist() for a in self.actions},
            "n": {a.value: self.n[a] for a in self.actions},
            "type_counts": dict(self.type_counts), "n_updates": self.n_updates,
        }

    @classmethod
    def from_json(cls, d: dict[str, Any]) -> LinearState:
        actions = [ActionType(a) for a in d["actions"]]
        s = cls(actions, d["dim"], d.get("ridge", 1.0))
        for a in actions:
            s.A[a] = np.array(d["A"][a.value], dtype=float)
            s.b[a] = np.array(d["b"][a.value], dtype=float)
            s.n[a] = int(d["n"][a.value])
        s.type_counts = {k: int(v) for k, v in d.get("type_counts", {}).items()}
        s.n_updates = int(d.get("n_updates", 0))
        return s

    def copy(self) -> LinearState:
        return LinearState.from_json(self.to_json())


class Policy(ABC):
    algorithm = "abstract"
    version_id: Any = None  # PolicyVersion.id when loaded/saved through PolicyStore

    def __init__(
        self,
        config: PolicyConfig | None = None,
        state: LinearState | None = None,
        *,
        version: str = "v0.0.0",
        rules: tuple[PriorRule, ...] = COLD_START_PRIORS,
    ):
        self.config = config or PolicyConfig()
        self.state = state or LinearState(ACTIONS, DIM, self.config.ridge)
        self.version = version
        self.rules = rules
        self._rng = np.random.default_rng(self.config.seed)

    # ---- masks -----------------------------------------------------------------------------------
    @property
    def allowed(self) -> tuple[ActionType, ...]:
        """Operator-configured action set (always contains `observe`)."""
        return resolve_allowed_actions(self.config.allowed_actions)

    @property
    def feature_schema(self) -> str:
        return self.state.schema if self.state.schema in FEATURE_SCHEMAS else LEGACY_SCHEMA

    def upgrade_schema(self) -> bool:
        """Pad this (in-memory copy of a) state to the current feature schema. True if anything changed."""
        if self.state.dim == SCHEMA_DIMS[SCHEMA]:
            return False
        self.state.pad_to(SCHEMA_DIMS[SCHEMA])
        return True

    def x_of(self, context: Any) -> np.ndarray:
        """Context in THIS policy's feature schema (older versions score only the features they were trained on)."""
        return coerce_context(context, self.feature_schema)

    def selectable(self, eligible: Iterable[ActionType | str] | None = None) -> tuple[ActionType, ...]:
        """Operator mask AND eligibility mask, applied BEFORE scoring. `observe` can never be masked."""
        base = self.allowed
        if eligible is None:
            return base
        ok = {ActionType(a) for a in eligible} | {ActionType.OBSERVE}
        return tuple(a for a in base if a in ok)

    # ---- scoring ---------------------------------------------------------------------------------
    def _unc_scale(self) -> float:
        return self.config.alpha

    def n_related(self, x: np.ndarray) -> int:
        t = incident_type_of(x)
        return self.state.type_counts.get(t.value if t else "unknown", 0)

    def score(self, context: Any, eligible: Iterable[ActionType | str] | None = None) -> list[ActionScore]:
        x = self.x_of(context)
        priors, _ = prior_scores(x, self.rules)
        out = []
        for a in self.selectable(eligible):
            n = self.state.n[a]
            w = n / (n + self.config.prior_strength) if n > 0 else 0.0
            lm, lsd = self.state.predict(a, x)
            lu = self._unc_scale() * lsd
            mean = w * lm + (1 - w) * priors[a]
            unc = w * lu + (1 - w) * self.config.prior_uncertainty
            out.append(ActionScore(a, mean, unc, mean + unc, priors[a], lm, w, n))
        return out

    @abstractmethod
    def _probabilities(self, scores: list[ActionScore]) -> np.ndarray:
        """Selection distribution over `scores` (same order, sums to 1, strictly positive)."""

    def probabilities(self, context: Any, eligible: Iterable[ActionType | str] | None = None) -> dict[ActionType, float]:
        scores = self.score(context, eligible)
        return {s.action: float(p) for s, p in zip(scores, self._probabilities(scores), strict=True)}

    def propensity(self, context: Any, action: ActionType,
                   eligible: Iterable[ActionType | str] | None = None) -> float:
        """Policy probability of an arbitrary action (0 if masked out)."""
        return self.probabilities(context, eligible).get(ActionType(action), 0.0)

    def _mix(self, p: np.ndarray) -> np.ndarray:
        eps = self.config.epsilon
        return (1 - eps) * p + eps / len(p)

    # ---- selection -------------------------------------------------------------------------------
    def select(self, context: Any, rng: np.random.Generator | None = None, *,
               eligible: Iterable[ActionType | str] | None = None,
               masked: Mapping[str, str] | None = None, uniform_draw: float | None = None) -> Decision:
        """Pick an action among the selectable ones. `eligible` is the eligibility mask (see `app.policy.mask`);
        masked actions get no score and cannot be chosen. `uniform_draw` replays a recorded selection exactly
        (inverse-CDF over the deterministic selection distribution)."""
        try:
            x = self.x_of(context)
            scores = self.score(x) if eligible is None else self.score(x, eligible)
            probs = self._probabilities(scores)
            if not np.all(np.isfinite(probs)) or abs(probs.sum() - 1.0) > 1e-6:
                raise ValueError("invalid selection distribution")
            u = float(uniform_draw) if uniform_draw is not None else float((rng or self._rng).random())
            cdf = np.cumsum(probs / probs.sum())
            idx = min(int(cdf.searchsorted(u, side="right")), len(scores) - 1)
            n_rel = self.n_related(x)
            cold = n_rel < self.config.min_related
            scores = [ActionScore(**{**asdict(s), "probability": float(p)}) for s, p in zip(scores, probs, strict=True)]
            return Decision(
                action=scores[idx].action, probability=float(probs[idx]), scores=scores,
                selection_basis=SelectionBasis.COLD_START_PRIOR if cold else SelectionBasis.LEARNED_POLICY,
                cold_start=cold, policy_version=self.version, n_related=n_rel,
                allowed_actions=self.selectable(eligible), matched_rules=prior_scores(x, self.rules)[1],
                algorithm=self.algorithm, context=x, feature_schema=self.feature_schema,
                masked_actions=self._masked(eligible, masked), uniform_draw=u, full_context=coerce_context(context),
            )
        except Exception as exc:  # noqa: BLE001 - any policy failure must degrade to the rule fallback
            return self.rule_fallback(context, reason=f"{type(exc).__name__}: {exc}", eligible=eligible, masked=masked)

    def _masked(self, eligible: Iterable[ActionType | str] | None, reasons: Mapping[str, str] | None) -> dict[str, str]:
        chosen = set(self.selectable(eligible))
        out = {a.value: "disabled by the operator action mask" for a in ACTIONS if a not in self.allowed}
        for a in ACTIONS:
            if a not in chosen and a.value not in out:
                out[a.value] = (reasons or {}).get(a.value, "not eligible for this incident")
        return out

    def rule_fallback(self, context: Any, reason: str = "", *, eligible: Iterable[ActionType | str] | None = None,
                      masked: Mapping[str, str] | None = None) -> Decision:
        """Deterministic: argmax of the cold-start prior among selectable actions, else observe.
        probability=1.0 is NOT a learned-policy propensity; OPE must exclude `rule_fallback` rows."""
        allowed = self.selectable(eligible)
        x = None
        try:
            x = self.x_of(context)
            pri, matched = prior_scores(x, self.rules)
            action = max(allowed, key=lambda a: (pri[a], a == ActionType.OBSERVE))
            scores = [ActionScore(a, pri[a], 0.0, pri[a], pri[a]) for a in allowed]
        except Exception:  # noqa: BLE001
            action, matched = ActionType.OBSERVE, []
            scores = [ActionScore(ActionType.OBSERVE, DEFAULT_PRIOR[ActionType.OBSERVE], 0.0,
                                  DEFAULT_PRIOR[ActionType.OBSERVE])]
        return Decision(action, 1.0, scores, SelectionBasis.RULE_FALLBACK, True, self.version, 0, allowed,
                        matched, self.algorithm, x, note=reason, feature_schema=self.feature_schema,
                        masked_actions=self._masked(eligible, masked), full_context=_full_or_none(context))

    # ---- learning --------------------------------------------------------------------------------
    def update(self, context: Any, action: ActionType | str, reward: float) -> None:
        """In-memory online update. Persisted versions are immutable: use `PolicyStore.save_update`."""
        x = self.x_of(context)
        a = ActionType(action)
        r = float(reward)
        if not np.isfinite(r):
            raise ValueError("reward must be finite")
        t = incident_type_of(x)
        self.state.update(a, x, r, t.value if t else "unknown")

    def copy(self) -> Policy:
        c = copy.copy(self)
        c.state = self.state.copy()
        c.config = PolicyConfig.from_json(self.config.to_json())
        c._rng = np.random.default_rng(self.config.seed)
        return c

    @property
    def n_updates(self) -> int:
        return self.state.n_updates

    def to_state(self) -> dict[str, Any]:
        return {**self.state.to_json(), "config": self.config.to_json(), "algorithm": self.algorithm,
                "feature_schema": self.feature_schema}

    @classmethod
    def from_state(cls, state: dict[str, Any], *, version: str = "v0.0.0",
                   rules: tuple[PriorRule, ...] = COLD_START_PRIORS, config: PolicyConfig | None = None) -> Policy:
        cfg = config or PolicyConfig.from_json(state.get("config", {}))
        return cls(cfg, LinearState.from_json(state), version=version, rules=rules)


class LinUCBPolicy(Policy):
    algorithm = "linucb"

    def _probabilities(self, scores: list[ActionScore]) -> np.ndarray:
        u = np.array([s.ucb for s in scores]) / max(self.config.temperature, 1e-6)
        u -= u.max()
        p = np.exp(u)
        return self._mix(p / p.sum())


class ThompsonPolicy(Policy):
    """Bayesian linear Thompson sampling. Posterior over reward at x is N(mean_a, uncertainty_a^2),
    with uncertainty_a = noise_std * sqrt(x^T A_a^-1 x) (blended with the prior as above)."""

    algorithm = "thompson"

    def _unc_scale(self) -> float:
        return self.config.noise_std

    def _probabilities(self, scores: list[ActionScore]) -> np.ndarray:
        K = len(scores)
        # Fixed common random numbers: pi(a|x) is a deterministic function of (state, x).
        z = np.random.default_rng(0 if self.config.seed is None else self.config.seed + 7919).standard_normal(
            (self.config.thompson_draws, len(ACTIONS))
        )
        cols = [ACTIONS.index(s.action) for s in scores]
        mean = np.array([s.mean for s in scores])
        unc = np.array([s.uncertainty for s in scores])
        draws = mean[None, :] + unc[None, :] * z[:, cols]
        wins = np.bincount(draws.argmax(axis=1), minlength=K) / draws.shape[0]
        return self._mix(wins)


POLICY_CLASSES: dict[str, type[Policy]] = {LinUCBPolicy.algorithm: LinUCBPolicy, ThompsonPolicy.algorithm: ThompsonPolicy}


def policy_from_state(algorithm: str, state: dict[str, Any], **kw: Any) -> Policy:
    return POLICY_CLASSES[algorithm].from_state(state, **kw)
