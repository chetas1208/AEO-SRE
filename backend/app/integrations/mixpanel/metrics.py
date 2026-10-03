"""Mixpanel-backed experiment metrics (counts in a time window)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry import LiveTelemetryEvent


def parse_mixpanel_metric_key(key: str) -> str | None:
    if key.startswith("mixpanel:"):
        return key.split(":", 1)[1]
    return None


async def count_events(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    event_name: str,
    start: datetime,
    end: datetime,
) -> int:
    q = select(func.count()).select_from(LiveTelemetryEvent).where(
        LiveTelemetryEvent.organization_id == org_id,
        LiveTelemetryEvent.source == "MIXPANEL",
        LiveTelemetryEvent.source_event == event_name,
        LiveTelemetryEvent.occurred_at >= start,
        LiveTelemetryEvent.occurred_at <= end,
    )
    return (await session.execute(q)).scalar_one()
