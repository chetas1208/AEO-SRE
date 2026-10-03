"""Experiment collision (contamination) detection.

Two interventions that change the same thing, or the same prompt cluster (whose metrics both would move), at the same
time cannot be separated afterwards. Rule: an experiment may not be ACTIVATED while another non-observe experiment is
active on the same target or the same (org, prompt cluster) scope.

    active  = approved | executing | executed | awaiting_verification   (real executions only; dry runs excluded)
    OBSERVE = coexists with everything: it changes nothing, so it can neither contaminate nor be refused. An OBSERVE
              experiment whose scope receives a real intervention during its window is no longer a baseline: its outcome
              is recorded INCONCLUSIVE with confounder `intervention_during_observe`.
    Two OBSERVE experiments on one scope coexist (both are pure measurement).
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, ExperimentStatus
from app.models.core import Incident
from app.models.interventions import Experiment

ACTIVE = (ExperimentStatus.APPROVED, ExperimentStatus.EXECUTING, ExperimentStatus.EXECUTED,
          ExperimentStatus.AWAITING_VERIFICATION)


class ExperimentCollision(Exception):
    """Refused: another active intervention shares the target or prompt-cluster scope."""

    def __init__(self, experiment_id: uuid.UUID, conflicts: list[Collision]):
        self.experiment_id, self.conflicts = experiment_id, conflicts
        super().__init__(
            f"experiment {experiment_id} collides with active experiment(s) "
            + ", ".join(f"{c.experiment_id} ({c.reason})" for c in conflicts)
            + "; wait for them to finish or reject one of them")


@dataclass(frozen=True)
class Collision:
    experiment_id: uuid.UUID
    reason: str  # same_target | same_cluster
    status: str


def target_key_for(intervention: Any, incident: Incident) -> str:
    """What this intervention touches: the page/url when the change names one, else the prompt-cluster scope."""
    change = getattr(intervention, "proposed_change", None) or {}
    url = change.get("target_url") or ((change.get("manual_task") or {}).get("target_url"))
    if not url and change.get("files"):
        url = change["files"][0].get("path")
    if url:
        return f"target:{str(url).strip().lower().rstrip('/')}"
    return scope_key(incident)


def scope_key(incident: Incident) -> str:
    return f"cluster:{incident.org_id}:{incident.prompt_cluster_id or 'org'}"


async def find_collisions(session: AsyncSession, experiment: Experiment) -> list[Collision]:
    """Active non-observe experiments (other than this one) that share this experiment's target or scope."""
    if ActionType(experiment.selected_action) == ActionType.OBSERVE:
        return []
    inc = await session.get(Incident, experiment.incident_id)
    scope = scope_key(inc)
    rows = (await session.execute(
        select(Experiment, Incident).join(Incident, Incident.id == Experiment.incident_id).where(
            Experiment.id != experiment.id, Experiment.status.in_(ACTIVE), Experiment.dry_run.is_(False),
            Experiment.selected_action != ActionType.OBSERVE)
    )).all()
    out: list[Collision] = []
    for other, other_inc in rows:
        same_target = bool(experiment.target_key) and other.target_key == experiment.target_key \
            and experiment.target_key.startswith("target:")
        if same_target:
            out.append(Collision(other.id, "same_target", str(ExperimentStatus(other.status).value)))
        elif scope_key(other_inc) == scope:
            out.append(Collision(other.id, "same_cluster", str(ExperimentStatus(other.status).value)))
    return out


async def assert_no_collision(session: AsyncSession, experiment: Experiment) -> None:
    conflicts = await find_collisions(session, experiment)
    if conflicts:
        raise ExperimentCollision(experiment.id, conflicts)


async def overlapping_interventions(session: AsyncSession, experiment: Experiment, until) -> list[Experiment]:
    """Non-observe experiments on the same scope whose [executed_at, window_end] interval overlaps this experiment's
    [executed_at, until]. Used for the `overlapping_intervention` / `intervention_during_observe` confounders."""
    if experiment.executed_at is None:
        return []
    inc = await session.get(Incident, experiment.incident_id)
    scope = scope_key(inc)
    rows = (await session.execute(
        select(Experiment, Incident).join(Incident, Incident.id == Experiment.incident_id).where(
            Experiment.id != experiment.id, Experiment.dry_run.is_(False),
            Experiment.selected_action != ActionType.OBSERVE, Experiment.executed_at.is_not(None),
            Experiment.status.in_((*ACTIVE, ExperimentStatus.VERIFIED, ExperimentStatus.REWARDED)))
    )).all()
    lo, hi = _aware(experiment.executed_at), _aware(until)
    out = []
    for other, oi in rows:
        if scope_key(oi) != scope and not (experiment.target_key and other.target_key == experiment.target_key):
            continue
        o_lo = _aware(other.executed_at)
        o_hi = _aware(other.verification_window_end) if other.verification_window_end else hi
        if o_lo <= hi and o_hi >= lo:
            out.append(other)
    return out


def _aware(dt):
    from app.experiments.window import aware

    return aware(dt)
