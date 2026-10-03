"""Turn persisted incident state into the explanation the UI shows.

No new facts. If investigation has not proposed a cause, the leading hypothesis stays empty.
"""
from __future__ import annotations

from typing import Any

from app.investigation.budget import independence_note
from app.schemas.incidents import IncidentExplanation, MetricDelta


def build_explanation(
    *,
    title: str,
    priority: float | None,
    metrics: list[MetricDelta],
    hypotheses: list[Any],
    hypotheses_meta: dict[str, Any] | None,
    selected_action: str | None,
    policy_scores: list[dict[str, Any]] | None,
    verification_window: str | None,
    selection_note: str | None = None,
    timings: dict[str, float | None] | None = None,
    evidence_families: list[str] | None = None,
) -> IncidentExplanation:
    meta = hypotheses_meta or {}
    candidates = []
    for row in hypotheses:
        info = meta.get(str(getattr(row, "id", "")), {})
        if info.get("actionable") is False or info.get("rule_id") == "no_actionable_cause":
            continue
        candidates.append(row)
    leading = max(candidates, key=lambda row: row.confidence or 0) if candidates else None
    lead_meta = meta.get(str(getattr(leading, "id", "")), {}) if leading is not None else {}
    metric = next((m.key for m in metrics if m.key), None)
    priority_text = "Priority was not scored." if priority is None else (
        f"Priority {priority:.0f} / 100. This is a ranking heuristic, not a revenue estimate."
    )
    if leading is None:
        note = "No root cause has been proposed. An empty investigation is not evidence of a cause."
        status = None
        name = None
        confidence = None
        support: list[str] = []
        contra: list[str] = []
    else:
        status = str(getattr(leading, "status", "proposed"))
        name = str(getattr(leading, "title", ""))
        confidence = getattr(leading, "confidence", None)
        support = [str(i) for i in (getattr(leading, "evidence_ids", None) or [])]
        contra = [str(i) for i in (lead_meta.get("contradicting_evidence_ids") or [])]
        note = (
            "This hypothesis is proposed. The evidence gate has not confirmed it."
            if status == "proposed"
            else "Hypothesis status is recorded as stored. Confirmation still requires the evidence gate."
        )
    if selection_note:
        note = (
            f"{note} Policy selection: {selection_note}. "
            "Listed scores are unconstrained policy probabilities, not the chosen action."
        )
    return IncidentExplanation(
        what_changed=title,
        why_it_matters=priority_text,
        leading_hypothesis=name,
        hypothesis_status=status,
        confidence=confidence,
        supporting_evidence=support,
        counterevidence=contra,
        recommended_action=selected_action,
        policy_scores=list(policy_scores or []),
        expected_metric=metric,
        verification_window=verification_window or "Verification has not started.",
        note=note,
        timings=dict(timings or {}),
        independence_note=independence_note(evidence_families or []),
    )
