"""Experiment status machine: proposed -> approved -> executing -> executed -> awaiting_verification
-> verified -> rewarded (+ rejected / failed). Transitions are validated and logged to `timeline`."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.enums import ActionType, ExperimentStatus

if TYPE_CHECKING:
    from app.models.interventions import Experiment

S = ExperimentStatus

ALLOWED: dict[ExperimentStatus, set[ExperimentStatus]] = {
    S.PROPOSED: {S.APPROVED, S.REJECTED, S.FAILED},
    S.APPROVED: {S.EXECUTING, S.FAILED},
    S.EXECUTING: {S.EXECUTED, S.FAILED},
    S.EXECUTED: {S.AWAITING_VERIFICATION, S.FAILED},
    S.AWAITING_VERIFICATION: {S.VERIFIED, S.FAILED},
    S.VERIFIED: {S.REWARDED},
    S.REWARDED: set(),
    S.REJECTED: set(),
    S.FAILED: set(),
}
TERMINAL = {s for s, dsts in ALLOWED.items() if not dsts}
POST_EXECUTION = {S.EXECUTED, S.AWAITING_VERIFICATION, S.VERIFIED, S.REWARDED}


class IllegalExperimentTransition(Exception):
    pass


def can_transition(src: ExperimentStatus | str, dst: ExperimentStatus | str) -> bool:
    return ExperimentStatus(dst) in ALLOWED[ExperimentStatus(src)]


def transition(
    experiment: Experiment,
    dst: ExperimentStatus | str,
    actor: str = "system",
    reason: str = "",
    *,
    now: datetime | None = None,
) -> Experiment:
    src, dst = ExperimentStatus(experiment.status), ExperimentStatus(dst)
    if dst not in ALLOWED[src]:
        raise IllegalExperimentTransition(f"{src.value} -> {dst.value} is not allowed")
    needs_approval = ActionType(experiment.selected_action) != ActionType.OBSERVE
    if dst == S.APPROVED and needs_approval and experiment.approval_id is None:
        raise IllegalExperimentTransition("cannot approve without a recorded approval")
    if dst == S.EXECUTING and needs_approval and (experiment.approval_id is None or not experiment.approver):
        raise IllegalExperimentTransition("cannot execute without a human-approved approval")
    if dst == S.AWAITING_VERIFICATION and (
        experiment.executed_at is None or experiment.verification_window_start is None or experiment.dry_run
    ):
        raise IllegalExperimentTransition("verification needs a real execution time and a verification window")
    if dst in (S.VERIFIED, S.REWARDED) and not experiment.after_metrics:
        raise IllegalExperimentTransition(f"{dst.value} requires measured post-intervention metrics")
    now = now or datetime.now(UTC)
    experiment.status = dst
    experiment.timeline = [
        *(experiment.timeline or []),
        {"from": src.value, "to": dst.value, "actor": actor, "reason": reason, "at": now.isoformat()},
    ]
    return experiment
