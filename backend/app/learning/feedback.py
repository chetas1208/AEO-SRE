"""Human feedback as SUPERVISED signal, kept apart from environment reward.

* A rejected / expired approval never changes the bandit: no Reward row, no PolicyVersion, no negative update. The
  experiment moves to `rejected` and nothing was executed, so there is no outcome to learn from. The reason a human
  gave (`Approval.note`) is stored with the approval and surfaced here as labelled data for review and for future
  supervised use.
* A human override (executed action != policy-selected action) is stored on the experiment (`policy_action`,
  `override_reason`, `override_by`). The outcome is attributed to the EXECUTED action only (`selected_action`); the
  propensity of the policy-selected action is dropped (`policy_probability` is NULL, basis `manual_override`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.interventions import Approval, Experiment, Intervention


@dataclass
class ActionFeedback:
    action: str
    proposed: int = 0
    rejected: int = 0
    reasons: list[str] = field(default_factory=list)

    @property
    def rejection_rate(self) -> float:
        return self.rejected / self.proposed if self.proposed else 0.0

    def to_json(self) -> dict[str, Any]:
        return {"action": self.action, "proposed": self.proposed, "rejected": self.rejected,
                "rejection_rate": round(self.rejection_rate, 4), "reasons": self.reasons[:20],
                "kind": "supervised_feedback_not_reward"}


async def rejection_feedback(session: AsyncSession) -> dict[str, ActionFeedback]:
    """Per action: how often humans rejected it and why. Read-only; never feeds the reward path."""
    rows = (await session.execute(select(Approval, Intervention).join(
        Intervention, Intervention.id == Approval.intervention_id))).all()
    out: dict[str, ActionFeedback] = {}
    for appr, iv in rows:
        fb = out.setdefault(str(iv.action), ActionFeedback(str(iv.action)))
        if str(appr.status) == "pending":
            continue
        fb.proposed += 1
        if str(appr.status) in ("rejected", "expired"):
            fb.rejected += 1
            if appr.note:
                fb.reasons.append(appr.note)
    return out


async def overrides(session: AsyncSession) -> list[dict[str, Any]]:
    """Experiments where the executed action differs from the policy's pick."""
    rows = (await session.execute(select(Experiment).where(Experiment.policy_action.is_not(None)))).scalars().all()
    return [{"experiment_id": str(e.id), "policy_action": e.policy_action, "executed_action": str(e.selected_action),
             "reason": e.override_reason, "by": e.override_by}
            for e in rows if e.policy_action != str(e.selected_action)]
