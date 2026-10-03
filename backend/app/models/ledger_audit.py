"""Automatic audit rows for consequential domain mutations (same transaction as the mutation).

Registered on the ORM ``before_flush`` hook so no call site can forget: a rolled-back mutation leaves no audit row and
a committed one always has one. Covers incident.created, evidence.created, hypothesis.proposed/confirmed/rejected,
observation.recorded, reward.created, experiment.activated (-> approved) and experiment.verified.
Explicit audits elsewhere cover state_transition, approvals, investigation.*, intervention.*, execution.*,
experiment.executed and policy_updated.
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

from app.domain.enums import ExperimentStatus, HypothesisStatus
from app.models.core import AuditEvent, Incident
from app.models.evidence import Evidence, Hypothesis
from app.models.interventions import Experiment, Observation, Reward

ACTOR = "domain-ledger"


def _ev(session: Session, entity_type: str, obj: Any, event_name: str, meta: dict) -> None:
    if obj.id is None:
        obj.id = uuid.uuid4()
    session.add(AuditEvent(
        id=uuid.uuid4(), actor_type="system", actor=ACTOR, entity_type=entity_type, entity_id=str(obj.id),
        event=event_name, metadata_=meta,
    ))


def _status_change(obj: Any, attr: str = "status") -> tuple[Any, Any] | None:
    hist = inspect(obj).attrs[attr].history
    if not hist.has_changes() or not hist.added:
        return None
    return (hist.deleted[0] if hist.deleted else None), hist.added[0]


@event.listens_for(Session, "before_flush")
def _audit_domain_mutations(session: Session, flush_context, instances) -> None:  # noqa: ARG001
    for obj in list(session.new):
        if isinstance(obj, Incident):
            _ev(session, "incident", obj, "incident.created", {
                "org_id": str(obj.org_id), "category": obj.category, "fingerprint": obj.fingerprint,
                "state": obj.state or "detected"})
        elif isinstance(obj, Evidence):
            _ev(session, "evidence", obj, "evidence.created", {
                "incident_id": str(obj.incident_id), "type": obj.type, "url": obj.url,
                "content_hash": obj.content_hash})
        elif isinstance(obj, Hypothesis):
            status = obj.status or HypothesisStatus.PROPOSED.value
            name = "hypothesis.proposed" if status == HypothesisStatus.PROPOSED.value else f"hypothesis.{status}"
            _ev(session, "hypothesis", obj, name, {"incident_id": str(obj.incident_id), "produced_by": obj.produced_by})
        elif isinstance(obj, Observation):
            _ev(session, "observation", obj, "observation.recorded", {
                "experiment_id": str(obj.experiment_id), "source": obj.source, "source_run_id": obj.source_run_id,
                "observed_at": obj.observed_at.isoformat() if obj.observed_at else None})
        elif isinstance(obj, Reward):
            _ev(session, "reward", obj, "reward.created", {
                "experiment_id": str(obj.experiment_id), "total": obj.total})
    for obj in list(session.dirty):
        if isinstance(obj, Hypothesis):
            ch = _status_change(obj)
            if ch and ch[0] != ch[1] and str(ch[1]) in (HypothesisStatus.CONFIRMED.value, HypothesisStatus.REJECTED.value):
                _ev(session, "hypothesis", obj, f"hypothesis.{ch[1]}", {
                    "incident_id": str(obj.incident_id), "from": str(ch[0]), "evidence_ids": list(obj.evidence_ids or [])})
        elif isinstance(obj, Experiment):
            for to in _transitions(obj):
                if to == ExperimentStatus.APPROVED:
                    _ev(session, "experiment", obj, "experiment.activated", {
                        "incident_id": str(obj.incident_id),
                        "action": str(getattr(obj.selected_action, "value", obj.selected_action)),
                        "approval_id": str(obj.approval_id) if obj.approval_id else None,
                        "baseline_metrics": sorted((obj.before_metrics or {}).keys())})
                elif to == ExperimentStatus.VERIFIED:
                    _ev(session, "experiment", obj, "experiment.verified", {
                        "incident_id": str(obj.incident_id),
                        "after_metrics": sorted((obj.after_metrics or {}).keys())})


def _transitions(exp: Experiment) -> list[ExperimentStatus]:
    """Statuses entered since the last flush, from the timeline entries appended by `status.transition`."""
    hist = inspect(exp).attrs.timeline.history
    if not hist.has_changes() or not hist.added:
        return []
    old = hist.deleted[0] if hist.deleted else None
    new = list(hist.added[0] or [])
    entries = new[len(old):] if old is not None else new[-1:]
    out = []
    for e in entries:
        try:
            out.append(ExperimentStatus(e.get("to")))
        except (ValueError, AttributeError):
            continue
    return out
