"""Canonical truth CRUD (admin-managed per organization). Human actor only; audited; soft-retire, never delete."""
from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.domain.errors import CanonicalClaimInvalid
from app.experiments import window as vwindow
from app.models.changeguard import CanonicalClaim

_KEY = re.compile(r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
NON_HUMAN = {"system", "model", "agent", "bot", "ai", "worker", "cron", "scheduler", "domain-ledger"}
EDITABLE = ("statement", "entities", "scope", "valid_from", "valid_until", "source")


def _actor(actor: str) -> str:
    a = (actor or "").strip()
    if not a or a.lower() in NON_HUMAN or a.lower().startswith("agent:"):
        raise CanonicalClaimInvalid("canonical truth is edited by a named human (X-Actor)", {"actor": a or None})
    return a


def _clean(fields: dict[str, Any]) -> dict[str, Any]:
    out = dict(fields)
    if "statement" in out:
        s = (out["statement"] or "").strip()
        if len(s.split()) < 2 or len(s) > 2000:
            raise CanonicalClaimInvalid("statement must be a sentence of at most 2000 characters",
                                        {"field": "statement"})
        out["statement"] = s
    if "entities" in out:
        out["entities"] = [str(e).strip() for e in (out["entities"] or []) if str(e).strip()][:50]
    if "scope" in out and out["scope"] is not None:
        out["scope"] = out["scope"].strip()[:512] or None
    if "source" in out and out["source"] is not None:
        out["source"] = out["source"].strip()[:512] or None
    return out


def public(c: CanonicalClaim) -> dict[str, Any]:
    return {k: getattr(c, k) for k in ("id", "org_id", "key", "statement", "entities", "scope", "valid_from",
                                       "valid_until", "source", "status", "created_by", "updated_by", "retired_at", "retired_by",
                                       "retire_reason", "created_at", "updated_at")}


async def list_claims(session: AsyncSession, org_id: uuid.UUID, *, status: str | None = None,
                      limit: int = 200, offset: int = 0) -> tuple[list[CanonicalClaim], int]:
    from sqlalchemy import func

    q = select(CanonicalClaim).where(CanonicalClaim.org_id == org_id)
    if status:
        q = q.where(CanonicalClaim.status == status)
    total = (await session.execute(select(func.count()).select_from(q.subquery()))).scalar_one()
    rows = (await session.execute(q.order_by(CanonicalClaim.status, CanonicalClaim.key).limit(limit).offset(offset))
            ).scalars().all()
    return list(rows), int(total)


async def create_claim(session: AsyncSession, org_id: uuid.UUID, actor: str, *, key: str, statement: str,
                       entities: list[str] | None = None, scope: str | None = None,
                       valid_from: datetime | None = None, source: str | None = None,
                       valid_until: datetime | None = None) -> CanonicalClaim:
    actor = _actor(actor)
    key = (key or "").strip().lower()
    if not _KEY.match(key):
        raise CanonicalClaimInvalid("key must be 1-128 chars of a-z 0-9 _ . : -", {"field": "key"})
    f = _clean({"statement": statement, "entities": entities or [], "scope": scope, "source": source})
    if valid_from and valid_until and valid_until <= valid_from:
        raise CanonicalClaimInvalid("valid_until must be after valid_from", {"field": "valid_until"})
    claim = CanonicalClaim(id=uuid.uuid4(), org_id=org_id, key=key, valid_from=valid_from, valid_until=valid_until, status="active",
                           created_by=actor, **f)
    try:
        async with session.begin_nested():
            session.add(claim)
            await session.flush()
    except IntegrityError:
        raise CanonicalClaimInvalid(f"an active canonical claim with key {key!r} already exists",
                                    {"field": "key", "key": key}) from None
    await audit(session, "human", actor, "canonical_claim", claim.id, "canonical_claim.changed",
                {"org_id": str(org_id), "key": key, "change": "created"})
    return claim


async def get_claim(session: AsyncSession, org_id: uuid.UUID, claim_id: uuid.UUID) -> CanonicalClaim | None:
    c = await session.get(CanonicalClaim, claim_id)
    return c if c is not None and c.org_id == org_id else None


async def update_claim(session: AsyncSession, claim: CanonicalClaim, actor: str, *, retire: bool = False,
                       retire_reason: str | None = None, **fields: Any) -> CanonicalClaim:
    actor = _actor(actor)
    if claim.status == "retired":
        raise CanonicalClaimInvalid("a retired claim cannot be changed; add a new claim", {"key": claim.key})
    changed: list[str] = []
    f = _clean({k: v for k, v in fields.items() if k in EDITABLE and v is not None})
    for k, v in f.items():
        if getattr(claim, k) != v:
            setattr(claim, k, v)
            changed.append(k)
    if retire:
        claim.status, claim.retired_at, claim.retired_by = "retired", vwindow.now(), actor
        claim.retire_reason = (retire_reason or "").strip()[:2000] or None
        changed.append("retired")
    if not changed:
        return claim
    claim.updated_by = actor
    await session.flush()
    await audit(session, "human", actor, "canonical_claim", claim.id, "canonical_claim.changed",
                {"org_id": str(claim.org_id), "key": claim.key, "change": "retired" if retire else "updated",
                 "fields": changed})
    return claim
