"""Metric-change evidence may name Profound only when the cited signals came from Profound."""
from __future__ import annotations

from app.models.core import Signal
from app.models.evidence import Evidence
from app.services.pipeline import _profound_evidence
from sqlalchemy import select

from tests import factories as f


async def _incident_with_source(session, source: str):
    org = await f.make_org(session)
    signal = Signal(org_id=org.id, kind="visibility", source=source, metric="visibility", value=0.37,
                    observed_at=f.NOW, raw={"fixture": "RECORDED/TEST ONLY"})
    session.add(signal)
    await session.flush()
    inc = await f.make_incident(session, org, metrics=[{
        "key": "visibility", "label": "Visibility", "before": 0.61, "after": 0.37, "delta": -0.24, "unit": "ratio",
    }], context={"signal_ids": [str(signal.id)]})
    return inc


async def test_fixture_signals_are_not_labeled_profound(session):
    inc = await _incident_with_source(session, "dev_fixture")
    n = await _profound_evidence(session, inc)
    await session.commit()
    rows = list((await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars())
    assert n == 1
    assert rows[0].source == "dev_fixture"
    assert rows[0].title.startswith("Measured:")
    assert "Profound" not in rows[0].title
    assert rows[0].raw["from_profound"] is False


async def test_profound_signals_keep_the_profound_label(session):
    inc = await _incident_with_source(session, "profound")
    await _profound_evidence(session, inc)
    await session.commit()
    row = (await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalar_one()
    assert row.source == "profound"
    assert row.title.startswith("Profound:")
    assert row.raw["from_profound"] is True
