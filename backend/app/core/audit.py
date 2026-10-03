"""Append-only audit trail."""

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import AuditEvent

ACTOR_TYPES = {"system", "human", "model"}


async def audit(
    session: AsyncSession,
    actor_type: str,
    actor: str,
    entity_type: str,
    entity_id: Any,
    event: str,
    metadata: dict | None = None,
    *,
    commit: bool = False,
) -> AuditEvent:
    if actor_type not in ACTOR_TYPES:
        raise ValueError(f"actor_type must be one of {sorted(ACTOR_TYPES)}")
    row = AuditEvent(
        id=uuid.uuid4(),
        actor_type=actor_type,
        actor=actor,
        entity_type=entity_type,
        entity_id=str(entity_id),
        event=event,
        metadata_=metadata or {},
    )
    session.add(row)
    if commit:
        await session.commit()
    else:
        await session.flush()
    return row
