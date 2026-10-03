"""Print one incident's stored chain. Development only.

    python -m app.devtools.trace <incident-id>
"""

from __future__ import annotations

import asyncio
import sys
import uuid

from sqlalchemy import select

from app.core.config import get_settings
from app.core.db import get_sessionmaker
from app.models.core import AuditEvent, Incident, IncidentEvent, Signal
from app.models.evidence import Evidence, Hypothesis
from app.models.interventions import Approval, Experiment, Intervention, Observation, Reward
from app.models.policy import PolicyDecision


async def trace(incident_id: uuid.UUID) -> int:
    if get_settings().environment != "development":
        print("trace-incident is available in development only")
        return 2
    async with get_sessionmaker()() as session:
        inc = await session.get(Incident, incident_id)
        if inc is None:
            print("incident not found")
            return 1
        print(f"incident {inc.number} {inc.state} {inc.title}")
        signal_ids = (inc.context or {}).get("signal_ids") or []
        signals = []
        if signal_ids:
            signals = (await session.execute(select(Signal.id, Signal.source, Signal.metric, Signal.observed_at).where(
                Signal.id.in_(signal_ids)
            ))).all()
        print(f"signals {len(signals)}")
        for row in signals:
            print(" ", row)
        evidence = (await session.execute(select(Evidence.id, Evidence.type, Evidence.retrieval_method, Evidence.retrieved_at).where(
            Evidence.incident_id == inc.id
        ))).all()
        print(f"evidence {len(evidence)}")
        for row in evidence:
            print(" ", row)
        hyps = (await session.execute(select(Hypothesis.title, Hypothesis.status, Hypothesis.confidence).where(
            Hypothesis.incident_id == inc.id
        ))).all()
        print(f"hypotheses {len(hyps)}")
        for row in hyps:
            print(" ", row)
        decisions = (await session.execute(select(PolicyDecision.selected_action, PolicyDecision.selection_basis, PolicyDecision.created_at).where(
            PolicyDecision.incident_id == inc.id
        ))).all()
        print(f"policy {len(decisions)}")
        for row in decisions:
            print(" ", row)
        interventions = (await session.execute(select(Intervention.id, Intervention.action, Intervention.selected).where(
            Intervention.incident_id == inc.id
        ))).all()
        print(f"interventions {len(interventions)}")
        for row in interventions:
            print(" ", row)
            approvals = (await session.execute(select(Approval.status, Approval.note, Approval.decided_by).where(
                Approval.intervention_id == row.id
            ))).all()
            for approval in approvals:
                print("   approval", approval)
        experiments = (await session.execute(select(Experiment.id, Experiment.status, Experiment.selected_action).where(
            Experiment.incident_id == inc.id
        ))).all()
        print(f"experiments {len(experiments)}")
        for row in experiments:
            print(" ", row)
            obs = (await session.execute(select(Observation.id, Observation.observed_at).where(
                Observation.experiment_id == row.id
            ))).all()
            rewards = (await session.execute(select(Reward.id, Reward.total).where(Reward.experiment_id == row.id))).all()
            print("   observations", obs)
            print("   rewards", rewards)
        events = (await session.execute(select(IncidentEvent.stage, IncidentEvent.status, IncidentEvent.at).where(
            IncidentEvent.incident_id == inc.id
        ).order_by(IncidentEvent.at))).all()
        print(f"timeline {len(events)}")
        for row in events:
            print(" ", row)
        audits = (await session.execute(select(AuditEvent.event, AuditEvent.at).where(
            AuditEvent.entity_id == str(inc.id)
        ).order_by(AuditEvent.at))).all()
        print(f"audit {len(audits)}")
        for row in audits:
            print(" ", row)
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m app.devtools.trace <incident-id>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(trace(uuid.UUID(sys.argv[1]))))


if __name__ == "__main__":
    main()
