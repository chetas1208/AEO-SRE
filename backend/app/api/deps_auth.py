from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep
from app.auth.sessions import SESSION_COOKIE, resolve_session
from app.models.auth import User


async def current_user(session: SessionDep, am_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None) -> User | None:
    row = await resolve_session(session, am_session)
    if row is None:
        return None
    user = await session.get(User, row.user_id)
    if user is None or user.status != "ACTIVE":
        return None
    return user


async def require_user(user: Annotated[User | None, Depends(current_user)]) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail={"error": {"code": "unauthorized", "message": "login required"}})
    return user


UserDep = Annotated[User, Depends(require_user)]
OptionalUserDep = Annotated[User | None, Depends(current_user)]
