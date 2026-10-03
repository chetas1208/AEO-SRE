"""Rules → Laya → LLM gate. Services call this before expensive model tasks."""
from __future__ import annotations

from dataclasses import dataclass

from app.core.config import get_settings


@dataclass(frozen=True)
class EscalationDecision:
    use_llm: bool
    reason: str
    laya_confidence: float | None = None


def should_escalate_to_llm(
    *,
    rule_answered: bool,
    laya_available: bool,
    laya_confidence: float | None,
    task_requires_semantics: bool,
) -> EscalationDecision:
    """Return whether an LLM call is warranted."""
    s = get_settings()
    if not s.model_escalation_enabled:
        return EscalationDecision(False, "model_escalation_disabled")
    if rule_answered and not task_requires_semantics:
        return EscalationDecision(False, "deterministic_rules")
    if task_requires_semantics:
        return EscalationDecision(True, "semantic_task")
    if not laya_available or laya_confidence is None:
        return EscalationDecision(True, "laya_unavailable")
    if laya_confidence >= s.laya_confidence_accept:
        return EscalationDecision(False, "laya_confident")
    if laya_confidence >= s.laya_confidence_review:
        return EscalationDecision(False, "laya_review_band")
    if laya_confidence < s.laya_confidence_escalate:
        return EscalationDecision(True, "laya_low_confidence")
    return EscalationDecision(True, "laya_below_accept")
