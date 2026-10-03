"""Persisted Mixpanel ingestion watermark (Postgres)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.telemetry import MixpanelCursor


async def get_cursor(session: AsyncSession, org_id: uuid.UUID) -> MixpanelCursor | None:
    return await session.get(MixpanelCursor, org_id)


async def upsert_cursor(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    last_successful_timestamp: datetime,
    last_event_id: str | None,
    last_query_window: dict,
) -> MixpanelCursor:
    row = await session.get(MixpanelCursor, org_id)
    if row is None:
        row = MixpanelCursor(org_id=org_id)
        session.add(row)
    row.last_successful_timestamp = last_successful_timestamp
    row.last_event_id = last_event_id
    row.last_query_window = last_query_window
    row.updated_at = datetime.now(UTC)
    return row


async def latest_sync_for_org(session: AsyncSession, org_id: uuid.UUID) -> datetime | None:
    row = await get_cursor(session, org_id)
    return row.last_successful_timestamp if row else None


async def global_last_sync(session: AsyncSession) -> datetime | None:
    q = await session.execute(select(MixpanelCursor.last_successful_timestamp).order_by(
        MixpanelCursor.last_successful_timestamp.desc()
    ).limit(1))
    return q.scalar()
