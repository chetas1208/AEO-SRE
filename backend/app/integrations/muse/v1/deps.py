from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.integrations.muse.v1.errors import muse_error
from app.oauth.scopes import scopes_allow
from app.oauth.service import MusePrincipal, OAuthError, resolve_access_token


async def muse_principal(
    session: SessionDep,
    authorization: Annotated[str | None, Header()] = None,
) -> MusePrincipal:
    if not get_settings().muse_connector_enabled:
        raise muse_error("upstream_unavailable", "Muse connector is disabled", 503)
    if not authorization or not authorization.lower().startswith("bearer "):
        raise muse_error("unauthorized", "Bearer access token required", 401)
    token = authorization.split(" ", 1)[1].strip()
    try:
        return await resolve_access_token(session, token)
    except OAuthError as exc:
        raise muse_error(exc.code, exc.message, exc.status) from exc


def require_scope(scope: str):
    async def _inner(principal: Annotated[MusePrincipal, Depends(muse_principal)]) -> MusePrincipal:
        if not scopes_allow(scope, principal.scopes):
            raise muse_error("insufficient_scope", f"{scope} scope is required.", 403)
        return principal

    return _inner
