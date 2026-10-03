"""Mixpanel integration API (catalog, data quality). Read-only."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.deps import SessionDep
from app.integrations.mixpanel.ingest import data_quality, resolve_org_id
from app.models.core import Setting

router = APIRouter(prefix="/api/integrations/mixpanel", tags=["mixpanel"])

CATALOG_KEY = "mixpanel.event_catalog"


@router.get("/catalog")
async def mixpanel_catalog(session: SessionDep):
    row = await session.get(Setting, CATALOG_KEY)
    events = (row.value or {}).get("event_names", []) if row else []
    return {
        "source": "MIXPANEL",
        "metrics": [{"id": f"mixpanel:{name}", "label": name, "source": "mixpanel"} for name in events],
        "event_names": events,
        "updated_at": (row.value or {}).get("updated_at") if row else None,
    }


@router.get("/quality")
async def mixpanel_quality(session: SessionDep):
    from app.core.config import get_settings

    oid = await resolve_org_id(session, get_settings())
    return await data_quality(session, oid)
