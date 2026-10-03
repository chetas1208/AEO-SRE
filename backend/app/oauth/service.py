from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta
from urllib.parse import urlencode, urlparse

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.models.auth import OrganizationMembership, User
from app.models.oauth import (
    OAuthAccessToken,
    OAuthAuthorization,
    OAuthAuthorizationCode,
    OAuthClient,
    OAuthPendingAuthorization,
    OAuthRefreshToken,
)
from app.oauth.pkce import verify_pkce
from app.oauth.scopes import validate_scopes
from app.oauth.tokens import hash_secret, new_auth_code, new_opaque_token


class OAuthError(Exception):
    def __init__(self, code: str, message: str, status: int = 400) -> None:
        self.code = code
        self.message = message
        self.status = status
        super().__init__(message)


@dataclass(frozen=True)
class MusePrincipal:
    user_id: uuid.UUID
    organization_id: uuid.UUID
    authorization_id: uuid.UUID
    client_id: str
    scopes: tuple[str, ...]


def issuer() -> str:
    s = get_settings()
    return (s.oauth_issuer or s.api_public_url).rstrip("/")


def _redirect_allowed(client: OAuthClient, redirect_uri: str) -> bool:
    return redirect_uri in (client.redirect_uris or [])


async def ensure_muse_client(session: AsyncSession) -> OAuthClient:
    s = get_settings()
    cid = s.muse_oauth_client_id.strip() or "muse-agentmatch"
    row = (await session.execute(select(OAuthClient).where(OAuthClient.client_id == cid))).scalar()
    uris = [u.strip() for u in s.muse_oauth_redirect_uris.split(",") if u.strip()]
    if row is None:
        row = OAuthClient(client_id=cid, name="Muse", redirect_uris=uris or ["http://127.0.0.1/callback"], status="ACTIVE")
        session.add(row)
        await session.flush()
    elif uris:
        row.redirect_uris = uris
    return row


async def start_authorization(
    session: AsyncSession,
    *,
    client_id: str,
    redirect_uri: str,
    scope: str,
    state: str,
    code_challenge: str,
    code_challenge_method: str,
) -> OAuthPendingAuthorization:
    if code_challenge_method != "S256" or len(code_challenge) < 43:
        raise OAuthError("invalid_request", "PKCE S256 code_challenge is required")
    client = (await session.execute(select(OAuthClient).where(
        OAuthClient.client_id == client_id, OAuthClient.status == "ACTIVE"))).scalar()
    if client is None:
        raise OAuthError("invalid_client", "unknown client", 401)
    if not _redirect_allowed(client, redirect_uri):
        raise OAuthError("invalid_request", "redirect_uri is not registered")
    raw_scopes = tuple(scope.split()) if scope.strip() else ()
    from app.oauth.scopes import READ_ONLY_BUNDLE

    scopes = validate_scopes(raw_scopes or READ_ONLY_BUNDLE)
    pending = OAuthPendingAuthorization(
        state=secrets.token_urlsafe(24) if not state else state[:128],
        client_id=client_id,
        redirect_uri=redirect_uri,
        scopes=list(scopes),
        code_challenge=code_challenge,
        code_challenge_method="S256",
        expires_at=utcnow() + timedelta(seconds=get_settings().oauth_authorization_code_ttl_seconds),
    )
    session.add(pending)
    await session.flush()
    return pending


async def bind_pending_user(
    session: AsyncSession, pending: OAuthPendingAuthorization, user: User, organization_id: uuid.UUID
) -> None:
    mem = (await session.execute(select(OrganizationMembership).where(
        OrganizationMembership.user_id == user.id, OrganizationMembership.organization_id == organization_id))
    ).scalar()
    if mem is None:
        raise OAuthError("access_denied", "not a member of this workspace", 403)
    pending.user_id = user.id
    pending.organization_id = organization_id


async def approve_pending(session: AsyncSession, pending: OAuthPendingAuthorization) -> str:
    if pending.user_id is None or pending.organization_id is None:
        raise OAuthError("access_denied", "login required", 403)
    if pending.expires_at <= utcnow():
        raise OAuthError("invalid_request", "authorization request expired")
    auth = OAuthAuthorization(
        user_id=pending.user_id,
        organization_id=pending.organization_id,
        client_id=pending.client_id,
        scopes=list(pending.scopes),
    )
    session.add(auth)
    await session.flush()
    raw_code = new_auth_code()
    code_row = OAuthAuthorizationCode(
        code_hash=hash_secret(raw_code),
        authorization_id=auth.id,
        client_id=pending.client_id,
        redirect_uri=pending.redirect_uri,
        code_challenge=pending.code_challenge,
        code_challenge_method=pending.code_challenge_method,
        scopes=list(pending.scopes),
        expires_at=utcnow() + timedelta(seconds=get_settings().oauth_authorization_code_ttl_seconds),
    )
    session.add(code_row)
    oauth_state = pending.state
    redirect = pending.redirect_uri
    await session.delete(pending)
    await session.flush()
    q = urlencode({"code": raw_code, "state": oauth_state})
    sep = "&" if "?" in redirect else "?"
    return f"{redirect}{sep}{q}"


async def exchange_authorization_code(
    session: AsyncSession,
    *,
    client_id: str,
    code: str,
    redirect_uri: str,
    code_verifier: str,
) -> dict[str, str | int]:
    row = (await session.execute(select(OAuthAuthorizationCode).where(
        OAuthAuthorizationCode.code_hash == hash_secret(code)))).scalar()
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        raise OAuthError("invalid_grant", "authorization code invalid or expired", 400)
    if row.client_id != client_id or row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", "client or redirect mismatch", 400)
    if not verify_pkce(code_verifier, row.code_challenge, row.code_challenge_method):
        raise OAuthError("invalid_grant", "PKCE verification failed", 400)
    row.used_at = utcnow()
    auth = await session.get(OAuthAuthorization, row.authorization_id)
    if auth is None or auth.revoked_at is not None:
        raise OAuthError("invalid_grant", "authorization revoked", 400)
    access_raw = new_opaque_token()
    refresh_raw = new_opaque_token()
    s = get_settings()
    access = OAuthAccessToken(
        token_hash=hash_secret(access_raw),
        authorization_id=auth.id,
        scopes=list(row.scopes),
        expires_at=utcnow() + timedelta(seconds=s.oauth_access_token_ttl_seconds),
    )
    refresh = OAuthRefreshToken(
        token_hash=hash_secret(refresh_raw),
        authorization_id=auth.id,
        expires_at=utcnow() + timedelta(seconds=s.oauth_refresh_token_ttl_seconds),
    )
    session.add(access)
    session.add(refresh)
    await session.flush()
    return {
        "access_token": access_raw,
        "token_type": "Bearer",
        "expires_in": s.oauth_access_token_ttl_seconds,
        "refresh_token": refresh_raw,
        "scope": " ".join(row.scopes),
    }


async def resolve_access_token(session: AsyncSession, bearer: str) -> MusePrincipal:
    if not bearer:
        raise OAuthError("invalid_token", "missing bearer token", 401)
    row = (await session.execute(select(OAuthAccessToken).where(
        OAuthAccessToken.token_hash == hash_secret(bearer)))).scalar()
    if row is None or row.revoked_at is not None or row.expires_at <= utcnow():
        raise OAuthError("invalid_token", "access token invalid or expired", 401)
    auth = await session.get(OAuthAuthorization, row.authorization_id)
    if auth is None or auth.revoked_at is not None:
        raise OAuthError("invalid_token", "authorization revoked", 401)
    auth.last_used_at = utcnow()
    return MusePrincipal(
        user_id=auth.user_id,
        organization_id=auth.organization_id,
        authorization_id=auth.id,
        client_id=auth.client_id,
        scopes=tuple(row.scopes or auth.scopes or []),
    )


async def revoke_token(session: AsyncSession, token: str, *, token_type_hint: str | None = None) -> None:
    h = hash_secret(token)
    now = utcnow()
    if token_type_hint != "refresh_token":
        access = (await session.execute(select(OAuthAccessToken).where(OAuthAccessToken.token_hash == h))).scalar()
        if access is not None:
            access.revoked_at = now
            auth = await session.get(OAuthAuthorization, access.authorization_id)
            if auth is not None:
                auth.revoked_at = now
            return
    refresh = (await session.execute(select(OAuthRefreshToken).where(OAuthRefreshToken.token_hash == h))).scalar()
    if refresh is not None:
        refresh.revoked_at = now
        auth = await session.get(OAuthAuthorization, refresh.authorization_id)
        if auth is not None:
            auth.revoked_at = now


async def list_connections(session: AsyncSession, user_id: uuid.UUID) -> list[OAuthAuthorization]:
    return list((await session.execute(select(OAuthAuthorization).where(
        OAuthAuthorization.user_id == user_id, OAuthAuthorization.revoked_at.is_(None),
        OAuthAuthorization.client_id == get_settings().muse_oauth_client_id))).scalars().all())
