"""Stop an investigation when the configured budget is spent. Do not invent a hypothesis to fill the gap."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum


def budget_exhausted(started: datetime, now: datetime, limit_seconds: float) -> bool:
    return (now - started).total_seconds() > limit_seconds


def independence_note(families: list[str]) -> str | None:
    unique = sorted({f for f in families if f})
    if len(unique) == 1:
        return (
            f"Supporting evidence is one family ({unique[0]}). "
            "Repeated excerpts from that family are not independent confirmation."
        )
    if len(unique) > 1:
        return f"Evidence families: {', '.join(unique)}."
    return None


# ---- investigation budget + stopping rules --------------------------------------------------------------------------


class StopReason(StrEnum):
    CONFIRMED = "confirmed"
    INSUFFICIENT_AFTER_BUDGET = "insufficient_after_budget"
    NO_NEW_USEFUL_EVIDENCE = "no_new_useful_evidence"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"  # finished within budget; the gate did not pass


@dataclass(frozen=True)
class InvestigationBudget:
    max_web_requests: int = 24
    max_llm_calls: int = 2
    max_evidence: int = 200
    max_hypotheses: int = 5
    max_seconds: float = 180.0

    @classmethod
    def from_settings(cls, settings) -> InvestigationBudget:
        return cls(
            max_web_requests=settings.max_web_sources, max_llm_calls=settings.max_model_calls,
            max_hypotheses=settings.max_hypotheses, max_seconds=settings.max_investigation_seconds,
        )


@dataclass
class BudgetTracker:
    budget: InvestigationBudget
    started: datetime
    web_requests: int = 0
    llm_calls: int = 0
    evidence: int = 0
    hypotheses: int = 0
    exhausted: list[str] = field(default_factory=list)

    def spend(self, kind: str, n: int = 1) -> bool:
        """Record spend; False (and the dimension noted) when it would exceed the budget."""
        cur = getattr(self, kind)
        limit = {"web_requests": self.budget.max_web_requests, "llm_calls": self.budget.max_llm_calls,
                 "evidence": self.budget.max_evidence, "hypotheses": self.budget.max_hypotheses}[kind]
        if cur + n > limit:
            if kind not in self.exhausted:
                self.exhausted.append(kind)
            return False
        setattr(self, kind, cur + n)
        return True

    def remaining(self, kind: str) -> int:
        limit = {"web_requests": self.budget.max_web_requests, "llm_calls": self.budget.max_llm_calls,
                 "evidence": self.budget.max_evidence, "hypotheses": self.budget.max_hypotheses}[kind]
        return max(0, limit - getattr(self, kind))

    def time_up(self, now: datetime) -> bool:
        if budget_exhausted(self.started, now, self.budget.max_seconds):
            if "seconds" not in self.exhausted:
                self.exhausted.append("seconds")
            return True
        return False

    def to_dict(self) -> dict:
        return {"web_requests": self.web_requests, "llm_calls": self.llm_calls, "evidence": self.evidence,
                "hypotheses": self.hypotheses, "exhausted": list(self.exhausted),
                "limits": {"web_requests": self.budget.max_web_requests, "llm_calls": self.budget.max_llm_calls,
                           "evidence": self.budget.max_evidence, "hypotheses": self.budget.max_hypotheses,
                           "seconds": self.budget.max_seconds}}


def decide_stop(*, confirmed: bool, tracker: BudgetTracker, evidence_fingerprint: str | None,
                previous_fingerprint: str | None) -> StopReason:
    """confirmed -> stop. Budget spent without confirmation -> insufficient. Same evidence as the previous
    investigation -> no new useful evidence (do not loop). Otherwise the pass ended without confirmation."""
    if confirmed:
        return StopReason.CONFIRMED
    if tracker.exhausted:
        return StopReason.INSUFFICIENT_AFTER_BUDGET
    if previous_fingerprint is not None and evidence_fingerprint == previous_fingerprint:
        return StopReason.NO_NEW_USEFUL_EVIDENCE
    return StopReason.INSUFFICIENT_EVIDENCE
