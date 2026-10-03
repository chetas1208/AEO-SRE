"""EventCorrelator: attach behavioral events to campaign / experiment context."""

from __future__ import annotations

import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ExperimentStatus
from app.integrations.mixpanel.schemas import NormalizedLiveEvent
from app.models.core import Incident
from app.models.interventions import Experiment


async def correlate_event(session: AsyncSession, ev: NormalizedLiveEvent) -> NormalizedLiveEvent:
    """Apply strongest-evidence-first linking. Never claims causality from time alone."""
    if ev.experiment_id:
        try:
            eid = uuid.UUID(ev.experiment_id)
            if await session.get(Experiment, eid):
                ev.correlation_method = "EXACT_ID"
                ev.correlation_confidence = "HIGH"
                return ev
        except ValueError:
            pass

    if ev.campaign_id:
        ev.correlation_method = "EXACT_ID"
        ev.correlation_confidence = "HIGH"
        return ev

    if ev.agent_id:
        ev.correlation_method = "EXACT_ID"
        ev.correlation_confidence = "MEDIUM"
        return ev

    corr_id = ev.correlation_keys.get("correlation_id") or ev.correlation_keys.get("request_id")
    if corr_id:
        ev.correlation_method = "SESSION"
        ev.correlation_confidence = "MEDIUM"
        return ev

    # Bounded temporal association to a running experiment only when org is known (weak).
    if ev.organization_id:
        try:
            oid = uuid.UUID(ev.organization_id)
        except ValueError:
            return ev
        window_start = ev.occurred_at - timedelta(hours=2)
        active = (
            ExperimentStatus.APPROVED,
            ExperimentStatus.EXECUTING,
            ExperimentStatus.EXECUTED,
            ExperimentStatus.AWAITING_VERIFICATION,
        )
        row = (
            await session.execute(
                select(Experiment.id)
                .join(Incident, Incident.id == Experiment.incident_id)
                .where(
                    Incident.org_id == oid,
                    Experiment.status.in_(active),
                    Experiment.created_at >= window_start,
                )
                .order_by(Experiment.created_at.desc())
                .limit(1)
            )
        ).first()
        if row:
            ev.experiment_id = str(row[0])
            ev.correlation_method = "TEMPORAL"
            ev.correlation_confidence = "LOW"
    return ev
