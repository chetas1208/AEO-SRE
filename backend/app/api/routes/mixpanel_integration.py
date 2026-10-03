"""Mixpanel integration API (catalog, data quality, live feed, status). Read-only."""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.integrations.mixpanel.auth import mixpanel_configured
from app.integrations.mixpanel.cursor import global_last_sync
from app.integrations.mixpanel.health import MixpanelHealth, derive_health
from app.integrations.mixpanel.ingest import data_quality, resolve_org_id
from app.models.core import Setting
from app.models.telemetry import LiveTelemetryEvent
from sqlalchemy import func, select

router = APIRouter(prefix="/api/integrations/mixpanel", tags=["mixpanel"])

CATALOG_KEY = "mixpanel.event_catalog"


@router.get("/catalog")
async def mixpanel_catalog(session: SessionDep) -> dict[str, Any]:
    """Return the discovered Mixpanel event catalog (populated by worker cron)."""
    row = await session.get(Setting, CATALOG_KEY)
    events = (row.value or {}).get("event_names", []) if row else []
    return {
        "source": "MIXPANEL",
        "metrics": [{"id": f"mixpanel:{name}", "label": name, "source": "mixpanel"} for name in events],
        "event_names": events,
        "updated_at": (row.value or {}).get("updated_at") if row else None,
    }


@router.get("/quality")
async def mixpanel_quality(session: SessionDep) -> dict[str, Any]:
    """Return ingestion data quality stats."""
    oid = await resolve_org_id(session, get_settings())
    return await data_quality(session, oid)


@router.get("/status")
async def mixpanel_status(session: SessionDep) -> dict[str, Any]:
    """
    Health and configuration status for the Mixpanel integration.
    This is the source-of-truth the frontend uses to decide what badge to show.
    """
    t0 = time.perf_counter()
    s = get_settings()
    configured = mixpanel_configured(s)
    last_sync = await global_last_sync(session)
    lag = (datetime.now(UTC) - last_sync).total_seconds() if last_sync else None

    if not configured:
        state, meta = derive_health(
            configured=False,
            auth_ok=None,
            rate_limited=False,
            last_sync_at=None,
            lag_seconds=None,
        )
        return {
            "configured": False,
            "health": state,
            "badge": "NOT_CONFIGURED",
            "detail": "Add MIXPANEL_SERVICE_ACCOUNT_USERNAME + SECRET + PROJECT_ID to .env and set MIXPANEL_ENABLED=true",
            "last_sync_at": None,
            "lag_seconds": None,
            "probe_latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            **meta,
        }

    from app.integrations.mixpanel.client import MixpanelClient
    client = MixpanelClient(s)
    auth_ok, latency_ms, auth_err = await client.ping_auth()
    state, meta = derive_health(
        configured=True,
        auth_ok=auth_ok,
        rate_limited=auth_err == "rate limited",
        last_sync_at=last_sync.isoformat() if last_sync else None,
        lag_seconds=lag,
        error=auth_err,
    )
    badge = "LIVE MIXPANEL" if state == MixpanelHealth.READY else (
        "RATE LIMITED" if state == MixpanelHealth.RATE_LIMITED else (
            "AUTH FAILED" if state == MixpanelHealth.AUTH_FAILED else "DEGRADED"
        )
    )
    return {
        "configured": True,
        "health": state,
        "badge": badge,
        "detail": meta.get("detail") or ("Near real-time polling · ~60s cadence" if auth_ok else auth_err),
        "last_sync_at": last_sync.isoformat() if last_sync else None,
        "lag_seconds": round(lag, 0) if lag is not None else None,
        "probe_latency_ms": round(latency_ms, 1),
        "sync_mode": "POLLING",
        **meta,
    }


@router.get("/live-feed")
async def mixpanel_live_feed(
    session: SessionDep,
    limit: int = Query(default=20, ge=1, le=100),
    hours_back: int = Query(default=24, ge=1, le=168),
) -> dict[str, Any]:
    """
    Return recent ingested Mixpanel events from the DB — NOT fixture data.
    Badge = LIVE MIXPANEL when events exist from real ingestion.
    Badge = NOT_CONFIGURED when no credentials.
    Returns empty list gracefully when configured but no events yet.
    """
    s = get_settings()
    configured = mixpanel_configured(s)
    last_sync = await global_last_sync(session)
    lag = (datetime.now(UTC) - last_sync).total_seconds() if last_sync else None

    # Determine badge and status
    if not configured:
        badge = "NOT_CONFIGURED"
        detail = "No Mixpanel credentials — add to .env"
    elif last_sync is None:
        badge = "CONFIGURED_NO_DATA"
        detail = "Mixpanel configured — waiting for first sync (worker runs every 60s)"
    elif lag is not None and lag < 300:
        badge = "LIVE MIXPANEL"
        detail = f"Near real-time · Last sync: {int(lag)}s ago"
    elif lag is not None and lag < 3600:
        badge = "LIVE MIXPANEL"
        detail = f"Near real-time · Last sync: {int(lag // 60)}m ago"
    else:
        badge = "DEGRADED"
        detail = f"Last sync: {int((lag or 0) // 3600)}h ago — check worker"

    # Query real events from DB
    since = datetime.now(UTC) - timedelta(hours=hours_back)
    filters = (
        LiveTelemetryEvent.source == "MIXPANEL",
        LiveTelemetryEvent.occurred_at >= since,
    )
    total_in_window = (
        await session.execute(select(func.count()).select_from(LiveTelemetryEvent).where(*filters))
    ).scalar_one()
    rows = (
        await session.execute(
            select(
                LiveTelemetryEvent.id,
                LiveTelemetryEvent.source_event,
                LiveTelemetryEvent.occurred_at,
                LiveTelemetryEvent.campaign_id,
                LiveTelemetryEvent.agent_id,
                LiveTelemetryEvent.experiment_id,
                LiveTelemetryEvent.correlation_method,
                LiveTelemetryEvent.correlation_confidence,
                LiveTelemetryEvent.tenant_resolution,
            )
            .where(*filters)
            .order_by(LiveTelemetryEvent.occurred_at.desc())
            .limit(limit)
        )
    ).all()

    events = []
    for r in rows:
        events.append({
            "id": str(r.id),
            "event": r.source_event,
            "occurred_at": r.occurred_at.isoformat() if r.occurred_at else None,
            "campaign_id": r.campaign_id,
            "agent_id": r.agent_id,
            "experiment_id": str(r.experiment_id) if r.experiment_id else None,
            "correlation_method": r.correlation_method,
            "correlation_confidence": r.correlation_confidence,
            "tenant_resolution": r.tenant_resolution,
            "source": "LIVE MIXPANEL",
        })

    return {
        "badge": badge,
        "detail": detail,
        "configured": configured,
        "last_sync_at": last_sync.isoformat() if last_sync else None,
        "lag_seconds": round(lag, 0) if lag is not None else None,
        "events": events,
        "total_in_window": total_in_window,
        "hours_back": hours_back,
        "fetched_at": datetime.now(UTC).isoformat(),
    }
