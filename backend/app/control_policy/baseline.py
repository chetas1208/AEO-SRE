"""BaselinePolicy: the deterministic Change Guard decision wrapped as a policy. NEVER delete: it is the active policy,
the fallback when the graph is unavailable/stale, and the reference every learner is measured against."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from app.changeguard.decision import PRECEDENCE, Decision

BASELINE_ALGORITHM = "baseline_rules"
BASELINE_VERSION = "change-guard-rules"


@dataclass(frozen=True)
class BaselineRecommendation:
    decision: Decision
    algorithm: str = BASELINE_ALGORITHM
    version: str = BASELINE_VERSION


def _finding_decision(f: Any) -> Decision:
    d = f.get("decision") if isinstance(f, Mapping) else getattr(f, "decision", None)
    return Decision(d)


class BaselinePolicy:
    """decision = highest-precedence finding (BLOCK > DELAY > REQUIRE_REVIEW > MERGE > ALLOW); no findings = ALLOW.
    Identical to `app.changeguard.decision.decide`, expressed over persisted finding dicts too."""

    algorithm = BASELINE_ALGORITHM
    version = BASELINE_VERSION

    def recommend_from_findings(self, findings: Iterable[Any]) -> BaselineRecommendation:
        best = Decision.ALLOW
        for f in findings:
            d = _finding_decision(f)
            if PRECEDENCE[d] > PRECEDENCE[best]:
                best = d
        return BaselineRecommendation(best)

    def recommend_from_check(self, check: Any) -> BaselineRecommendation:
        """The decision Change Guard already stored for this evaluation (the source of truth)."""
        return BaselineRecommendation(Decision(check.decision))
