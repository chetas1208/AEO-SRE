"""OAuth 2.0 authorization server (PKCE) for Muse connector."""
from __future__ import annotations

from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.api.deps_auth import UserDep
from app.core.config import get_settings
from app.models.oauth import OAuthPendingAuthorization
from app.oauth.scopes import READ_ONLY_BUNDLE, SCOPE_DESCRIPTIONS
from app.oauth.service import (
    OAuthError,
    approve_pending,
    bind_pending_user,
    ensure_muse_client,
    exchange_authorization_code,
    issuer,
    revoke_token,
    start_authorization,
)
from sqlalchemy import select

router = APIRouter(tags=["oauth"])


@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata() -> dict:
    base = issuer()
    return {
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "revocation_endpoint": f"{base}/oauth/revoke",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": list(SCOPE_DESCRIPTIONS.keys()),
    }


@router.get("/oauth/authorize")
async def oauth_authorize(
    session: SessionDep,
    client_id: str = Query(...),
    redirect_uri: str = Query(...),
    response_type: str = Query(...),
    scope: str = Query(default=" ".join(READ_ONLY_BUNDLE)),
    state: str = Query(default=""),
    code_challenge: str = Query(...),
    code_challenge_method: str = Query(default="S256"),
):
    if response_type != "code":
        raise OAuthError("unsupported_response_type", "only code is supported", 400)
    try:
        pending = await start_authorization(
            session, client_id=client_id, redirect_uri=redirect_uri, scope=scope, state=state,
            code_challenge=code_challenge, code_challenge_method=code_challenge_method)
        await session.commit()
    except OAuthError as exc:
        return JSONResponse({"error": exc.code, "error_description": exc.message}, status_code=exc.status)
    app_url = get_settings().app_public_url.rstrip("/")
    q = urlencode({"oauth_state": pending.state})
    return RedirectResponse(f"{app_url}/oauth/authorize?{q}", status_code=302)


class PendingOut(BaseModel):
    state: str
    client_name: str
    scopes: list[str]
    scope_labels: dict[str, str]
    redirect_uri: str


@router.get("/api/oauth/pending", response_model=PendingOut)
async def oauth_pending(session: SessionDep, oauth_state: str = Query(...)) -> PendingOut:
    row = (await session.execute(select(OAuthPendingAuthorization).where(
        OAuthPendingAuthorization.state == oauth_state))).scalar()
    if row is None:
        raise OAuthError("invalid_request", "unknown oauth_state", 404)
    client = await ensure_muse_client(session)
    return PendingOut(
        state=row.state, client_name=client.name, scopes=list(row.scopes),
        scope_labels=SCOPE_DESCRIPTIONS, redirect_uri=row.redirect_uri)


class ConsentIn(BaseModel):
    oauth_state: str
    organization_id: str
    approved: bool = True
    scopes: list[str] = Field(default_factory=list)


@router.post("/api/oauth/consent")
async def oauth_consent(body: ConsentIn, session: SessionDep, user: UserDep):
    import uuid

    row = (await session.execute(select(OAuthPendingAuthorization).where(
        OAuthPendingAuthorization.state == body.oauth_state))).scalar()
    if row is None:
        raise OAuthError("invalid_request", "unknown oauth_state", 404)
    if not body.approved:
        q = urlencode({"error": "access_denied", "state": row.state})
        sep = "&" if "?" in row.redirect_uri else "?"
        await session.delete(row)
        await session.commit()
        return {"redirect_to": f"{row.redirect_uri}{sep}{q}"}
    try:
        oid = uuid.UUID(body.organization_id)
        await bind_pending_user(session, row, user, oid)
        if body.scopes:
            row.scopes = body.scopes
        redirect_to = await approve_pending(session, row)
        await session.commit()
        return {"redirect_to": redirect_to}
    except OAuthError as exc:
        return JSONResponse({"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status)


@router.post("/oauth/token")
async def oauth_token(
    session: SessionDep,
    grant_type: Annotated[str, Form()],
    client_id: Annotated[str, Form()] = "",
    code: Annotated[str | None, Form()] = None,
    redirect_uri: Annotated[str | None, Form()] = None,
    code_verifier: Annotated[str | None, Form()] = None,
):
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
    try:
        out = await exchange_authorization_code(
            session, client_id=client_id, code=code or "", redirect_uri=redirect_uri or "",
            code_verifier=code_verifier or "")
        await session.commit()
        return out
    except OAuthError as exc:
        return JSONResponse({"error": exc.code, "error_description": exc.message}, status_code=exc.status)


@router.post("/oauth/revoke")
async def oauth_revoke(session: SessionDep, token: Annotated[str, Form()], token_type_hint: Annotated[str | None, Form()] = None):
    await revoke_token(session, token, token_type_hint=token_type_hint)
    await session.commit()
    return JSONResponse({}, status_code=200)


class ConnectionOut(BaseModel):
    authorization_id: str
    organization_id: str
    scopes: list[str]
    connected_at: str
    last_used_at: str | None


@router.get("/api/oauth/connections/muse", response_model=list[ConnectionOut])
async def muse_connections(session: SessionDep, user: UserDep) -> list[ConnectionOut]:
    from app.oauth.service import list_connections

    rows = await list_connections(session, user.id)
    return [
        ConnectionOut(
            authorization_id=str(r.id), organization_id=str(r.organization_id), scopes=list(r.scopes or []),
            connected_at=r.created_at.isoformat(), last_used_at=r.last_used_at.isoformat() if r.last_used_at else None)
        for r in rows
    ]


@router.post("/api/oauth/connections/muse/{authorization_id}/revoke", status_code=204)
async def muse_disconnect(session: SessionDep, user: UserDep, authorization_id: str) -> None:
    import uuid
    from app.models.oauth import OAuthAuthorization

    auth = await session.get(OAuthAuthorization, uuid.UUID(authorization_id))
    if auth is None or auth.user_id != user.id:
        raise OAuthError("not_found", "connection not found", 404)
    from app.core.db import utcnow

    auth.revoked_at = utcnow()
    await session.commit()
