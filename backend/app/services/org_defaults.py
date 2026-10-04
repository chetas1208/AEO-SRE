"""Default organization resolution for live Profound / hackathon brand telemetry."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.core import Organization


async def resolve_live_brand_org_id(session: AsyncSession) -> uuid.UUID | None:
    """Org that owns live Profound signals (Mixpanel brand org by default)."""
    s = get_settings()
    raw = (s.mixpanel_default_org_id or "").strip()
    if raw:
        try:
            oid = uuid.UUID(raw)
            if await session.get(Organization, oid) is not None:
                return oid
        except ValueError:
            pass
    domain = (s.mixpanel_org_domain or "mixpanel.com").strip().lower()
    row = (await session.execute(select(Organization.id).where(Organization.domain == domain))).first()
    return row[0] if row else None
