"""Failure-safe modes (Plan section 7) at the pipeline level: a failing step never stops the others."""
import uuid

from app.domain.enums import IncidentState
from app.models.core import Incident, IncidentEvent
from app.services import pipeline
from sqlalchemy import select

from tests.integration.test_full_loop import (
    world,  # noqa: F401  (fixture: org + recorded signals + mocked web)
)


async def test_failed_web_collection_marks_incomplete_and_continues(session, world, monkeypatch):  # noqa: F811
    org, _cluster, _now = world

    async def explode(*_a, **_k):
        raise RuntimeError("crawler exploded")

    import app.investigation.collector as col

    monkeypatch.setattr(col, "collect_evidence", explode)
    out = await pipeline.detect(session, org.id)
    iid = uuid.UUID(out["created"][0])
    inc = await session.get(Incident, iid, populate_existing=True)
    assert inc.investigation_status == "incomplete"
    assert "evidence.web" in inc.context["investigation"]["failed_steps"]
    # profound evidence, hypotheses, graph and gate still ran; nothing was confirmed from missing content
    assert inc.state in (IncidentState.AWAITING_APPROVAL.value, IncidentState.ROOT_CAUSE_PROPOSED.value,
                         IncidentState.INTERVENTION_PROPOSED.value)
    events = (await session.execute(select(IncidentEvent).where(IncidentEvent.incident_id == iid))).scalars().all()
    failed = [e for e in events if e.status == "failed"]
    assert any(e.stage == "evidence.web.completed" for e in failed)
    assert any(e.stage == "hypotheses.completed" for e in events)
