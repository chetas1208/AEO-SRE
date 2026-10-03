"""Decision vocabulary, findings and precedence (pure)."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

GUARD_VERSION = "change-guard/1.0"


class Decision(StrEnum):
    ALLOW = "ALLOW"
    MERGE = "MERGE"
    REQUIRE_REVIEW = "REQUIRE_REVIEW"
    DELAY = "DELAY"
    BLOCK = "BLOCK"


# BLOCK > DELAY > REQUIRE_REVIEW > MERGE > ALLOW
PRECEDENCE = {Decision.BLOCK: 4, Decision.DELAY: 3, Decision.REQUIRE_REVIEW: 2, Decision.MERGE: 1, Decision.ALLOW: 0}

# Finding types
ACTIVE_EXPERIMENT = "active_experiment_contamination"
DUPLICATE_CHANGE = "duplicate_target_change"
CONFLICTING_CHANGE = "conflicting_target_change"
CANONICAL_CONFLICT = "canonical_conflict"
CANONICAL_UNCERTAIN = "canonical_uncertain"
CANONICAL_UNAVAILABLE = "canonical_truth_unavailable"
SEMANTIC_DEGRADED = "semantic_check_degraded"
NO_CLAIMS = "no_claims_to_check"
CHECK_NUMBER = {ACTIVE_EXPERIMENT: 1, DUPLICATE_CHANGE: 2, CONFLICTING_CHANGE: 2, CANONICAL_CONFLICT: 3,
                CANONICAL_UNCERTAIN: 3,                CANONICAL_UNAVAILABLE: 3, SEMANTIC_DEGRADED: 3, NO_CLAIMS: 3}


@dataclass
class Finding:
    type: str
    decision: Decision  # the decision this finding alone implies (ALLOW = informational)
    reason: str
    severity: str = ""  # block | delay | review | merge | info
    references: dict[str, Any] = field(default_factory=dict)
    eligible_after: datetime | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.decision = Decision(self.decision)
        if not self.severity:
            self.severity = {Decision.BLOCK: "block", Decision.DELAY: "delay", Decision.REQUIRE_REVIEW: "review",
                             Decision.MERGE: "merge", Decision.ALLOW: "info"}[self.decision]

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type, "check": CHECK_NUMBER.get(self.type), "decision": self.decision.value,
            "severity": self.severity, "reason": self.reason, "references": self.references,
            "eligible_after": iso(self.eligible_after), "details": self.details,
        }


def iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    from app.experiments.window import aware

    return aware(dt).isoformat().replace("+00:00", "Z")


def decide(findings: list[Finding]) -> Decision:
    """Highest precedence wins; no findings (or only informational ones) = ALLOW."""
    best = Decision.ALLOW
    for f in findings:
        if PRECEDENCE[f.decision] > PRECEDENCE[best]:
            best = f.decision
    return best


def latest_eligible_after(findings: list[Finding]) -> datetime | None:
    times = [f.eligible_after for f in findings if f.decision == Decision.DELAY and f.eligible_after is not None]
    if not times:
        return None
    from app.experiments.window import aware

    return max(aware(t) for t in times)
