from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.models.auth import UserSession

SESSION_COOKIE = "am_session"
SESSION_TTL = timedelta(days=14)


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


async def create_session(session: AsyncSession, user_id: uuid.UUID) -> tuple[UserSession, str]:
    raw = new_session_token()
    row = UserSession(user_id=user_id, expires_at=utcnow() + SESSION_TTL)
    session.add(row)
    await session.flush()
    return row, raw


async def resolve_session(session: AsyncSession, token: str | None) -> UserSession | None:
    if not token:
        return None
    try:
        sid = uuid.UUID(token)
    except ValueError:
        return None
    row = await session.get(UserSession, sid)
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        return None
    return row


async def revoke_session(session: AsyncSession, token: str | None) -> None:
    row = await resolve_session(session, token)
    if row is not None:
        row.revoked_at = utcnow()


async def revoke_all_user_sessions(session: AsyncSession, user_id: uuid.UUID) -> None:
    rows = (await session.execute(select(UserSession).where(
        UserSession.user_id == user_id, UserSession.revoked_at.is_(None)))).scalars().all()
    now = utcnow()
    for r in rows:
        r.revoked_at = now
