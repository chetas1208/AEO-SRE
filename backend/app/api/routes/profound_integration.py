"""Profound API live telemetry (read-only from ingested signals). Primary live data source for the control plane."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.services.campaign_profound import live_profound_overlay, resolve_brand_org_id
from app.services.pipeline import ingest

router = APIRouter(prefix="/api/integrations/profound", tags=["profound"])


@router.get("/live")
async def profound_live(session: SessionDep):
    """Latest Profound metrics + 7d delta from Postgres (api.tryprofound.com ingest)."""
    return await live_profound_overlay(session)


@router.post("/refresh")
async def profound_refresh(session: SessionDep):
    """Pull from Profound API, persist signals, return live overlay."""
    oid = await resolve_brand_org_id(session)
    if oid is None:
        return {"status": "NO_ORG", "ingest": None, "profound_live": await live_profound_overlay(session)}
    ingest_result = await ingest(session, oid)
    overlay = await live_profound_overlay(session, org_id=oid)
    return {"ingest": ingest_result, "profound_live": overlay}
