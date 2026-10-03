"""Bearer-key auth for the connector. One key maps to ONE organization (config); no cross-org access."""
from __future__ import annotations

import hashlib
import hmac
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.integrations.muse import errors as E
from app.models.core import Organization


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


def key_id(key: str) -> str:
    """Stable non-reversible bucket id for rate limiting and logs (never the key itself)."""
    return hashlib.sha256(b"muse-key|" + key.encode()).hexdigest()[:12]


def authenticate(authorization: str | None, settings: Settings) -> str:
    """Return the key id, or raise NotConfigured (503) / Unauthorized (401). Constant-time compare of digests."""
    configured = settings.muse_connector_api_key
    if not configured:
        raise E.NotConfigured("the Muse connector is not configured: set MUSE_CONNECTOR_API_KEY to enable /muse/tools/*")
    scheme, _, supplied = (authorization or "").partition(" ")
    supplied = supplied.strip()
    ok = scheme.lower() == "bearer" and bool(supplied)
    same = hmac.compare_digest(_digest(supplied if ok else ""), _digest(configured))  # always compare
    if not (ok and same):
        raise E.Unauthorized("missing or invalid bearer key")
    return key_id(configured)


async def resolve_org(session: AsyncSession, settings: Settings) -> uuid.UUID:
    """The single organization this connector key is bound to (MUSE_ORGANIZATION_ID, else MUSE_ORGANIZATION_DOMAIN)."""
    raw_id, domain = settings.muse_organization_id.strip(), settings.muse_organization_domain.strip().lower()
    if raw_id:
        try:
            oid = uuid.UUID(raw_id)
        except ValueError as exc:
            raise E.OrganizationNotConfigured("MUSE_ORGANIZATION_ID is not a valid UUID") from exc
        if await session.get(Organization, oid) is None:
            raise E.OrganizationNotConfigured("the configured Muse organization does not exist")
        return oid
    if domain:
        row = (await session.execute(select(Organization.id).where(
            Organization.domain == domain.removeprefix("www.")))).first()
        if row is None:
            raise E.OrganizationNotConfigured("the configured Muse organization does not exist")
        return row[0]
    raise E.OrganizationNotConfigured("bind the connector to an organization: set MUSE_ORGANIZATION_ID or "
                                      "MUSE_ORGANIZATION_DOMAIN")
