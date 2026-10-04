"""Profound API live telemetry (read-only from ingested signals). Primary live data source for the control plane."""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.services.campaign_profound import live_profound_overlay, resolve_brand_org_id
from app.services.pipeline import ingest
from app.services.profound_agents import list_profound_agents, profound_generation_enabled

router = APIRouter(prefix="/api/integrations/profound", tags=["profound"])


@router.get("/live")
async def profound_live(session: SessionDep):
    """Latest Profound metrics + 7d delta from Postgres (api.tryprofound.com ingest)."""
    return await live_profound_overlay(session)


@router.post("/runs/sync")
async def profound_sync_runs(session: SessionDep):
    """Refresh LIVE Profound agent run status across campaigns and experiments."""
    from app.services.profound_agents import sync_all_live_runs

    return await sync_all_live_runs(session)


@router.post("/agents/bootstrap")
async def profound_agents_bootstrap(force: bool = Query(default=False)):
    """Create and publish default AEO marketing agents in Profound (LIVE API)."""
    from app.services.profound_agent_factory import bootstrap_default_agents

    if not profound_generation_enabled():
        return {"status": "DISABLED", "message": "Set PROFOUND_API_KEY to create agents."}
    return await bootstrap_default_agents(force=force)


@router.get("/agents")
async def profound_agents():
    """Live Profound agent catalog (for campaign/experiment generation pickers)."""
    body = await list_profound_agents(limit=100)
    body["generation_enabled"] = profound_generation_enabled()
    return body


@router.post("/refresh")
async def profound_refresh(session: SessionDep):
    """Pull from Profound API, persist signals, return live overlay."""
    oid = await resolve_brand_org_id(session)
    if oid is None:
        return {"status": "NO_ORG", "ingest": None, "profound_live": await live_profound_overlay(session)}
    ingest_result = await ingest(session, oid)
    overlay = await live_profound_overlay(session, org_id=oid)
    return {"ingest": ingest_result, "profound_live": overlay}
