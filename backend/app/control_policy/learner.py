"""GraphLinUCB v0: LinUCB over control_context_v1 (+ optional Laya prior features)."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

import numpy as np

from app.changeguard.decision import Decision
from app.control_policy.encoder import DIM, FEATURE_NAMES

CONTROL_ACTIONS: tuple[str, ...] = tuple(d.value for d in (
    Decision.ALLOW, Decision.MERGE, Decision.DELAY, Decision.REQUIRE_REVIEW, Decision.BLOCK))
LAYA_SUFFIX = tuple(f"laya_{a.lower()}" for a in CONTROL_ACTIONS)
EXT_DIM = DIM + len(CONTROL_ACTIONS)
ALGORITHM = "GraphLinUCB"
POLICY_VERSION = "graph-linucb-v0-shadow"


@dataclass
class ActionScore:
    action: str
    mean: float
    ucb: float
    eligible: bool

    def to_json(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BanditRecommendation:
    action: str
    scores: list[ActionScore]
    algorithm: str = ALGORITHM
    version: str = POLICY_VERSION

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "algorithm": self.algorithm, "version": self.version,
                "scores": [s.to_json() for s in self.scores]}


class ControlLinUCB:
    """Disjoint LinUCB per control action; cold start uses zero mean + exploration bonus."""

    def __init__(self, *, alpha: float = 0.5, ridge: float = 1.0, dim: int = EXT_DIM):
        self.alpha = alpha
        self.ridge = ridge
        self.dim = dim
        self.A = {a: ridge * np.eye(dim) for a in CONTROL_ACTIONS}
        self.b = {a: np.zeros(dim) for a in CONTROL_ACTIONS}

    def _vector(self, base: list[float], laya_distribution: dict[str, float] | None) -> np.ndarray:
        x = list(base)
        if len(x) < DIM:
            x = x + [0.0] * (DIM - len(x))
        elif len(x) > DIM:
            x = x[:DIM]
        if laya_distribution:
            x.extend(float(laya_distribution.get(a, 0.0)) for a in CONTROL_ACTIONS)
        else:
            x.extend([0.0] * len(CONTROL_ACTIONS))
        if len(x) < self.dim:
            x.extend([0.0] * (self.dim - len(x)))
        return np.array(x[: self.dim], dtype=float)

    def recommend(
        self,
        feature_vector: list[float],
        *,
        eligible: tuple[str, ...],
        laya_distribution: dict[str, float] | None = None,
    ) -> BanditRecommendation:
        x = self._vector(feature_vector, laya_distribution)
        scores: list[ActionScore] = []
        best_a, best_ucb = eligible[0], float("-inf")
        for a in CONTROL_ACTIONS:
            ok = a in eligible
            if not ok:
                scores.append(ActionScore(a, 0.0, 0.0, False))
                continue
            Ainv = np.linalg.inv(self.A[a])
            theta = Ainv @ self.b[a]
            mean = float(theta @ x)
            bonus = self.alpha * float(np.sqrt(max(x @ Ainv @ x, 0.0)))
            ucb = mean + bonus
            scores.append(ActionScore(a, mean, ucb, True))
            if ucb > best_ucb:
                best_ucb, best_a = ucb, a
        return BanditRecommendation(best_a, scores)


# Process-wide cold-start policy (persisted learner state lives in ControlPolicyVersion.state_snapshot).
_DEFAULT = ControlLinUCB()


def get_control_learner() -> ControlLinUCB:
    return _DEFAULT


def extended_feature_names() -> tuple[str, ...]:
    return FEATURE_NAMES + LAYA_SUFFIX
