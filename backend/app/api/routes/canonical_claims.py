"""Canonical truth per organization (admin-managed; human actor via X-Actor; audited; soft-retire)."""
import uuid

from fastapi import APIRouter

from app.api.deps import ActorDep, PageDep, SessionDep
from app.api.errors import NotFound
from app.api.routes.organizations import get_org
from app.changeguard import canonical
from app.schemas.changeguard import (
    CanonicalClaimIn,
    CanonicalClaimList,
    CanonicalClaimOut,
    CanonicalClaimPatch,
)

router = APIRouter(prefix="/api/organizations", tags=["canonical-truth"])


@router.get("/{org_id}/canonical-claims", response_model=CanonicalClaimList)
async def list_canonical_claims(org_id: uuid.UUID, session: SessionDep, page: PageDep, status: str | None = None):
    await get_org(session, org_id)
    rows, total = await canonical.list_claims(session, org_id, status=status, limit=page.limit, offset=page.offset)
    return CanonicalClaimList(items=[CanonicalClaimOut.model_validate(r) for r in rows], total=total,
                              limit=page.limit, offset=page.offset)


@router.post("/{org_id}/canonical-claims", response_model=CanonicalClaimOut, status_code=201)
async def create_canonical_claim(org_id: uuid.UUID, body: CanonicalClaimIn, session: SessionDep, actor: ActorDep):
    await get_org(session, org_id)
    claim = await canonical.create_claim(session, org_id, actor, key=body.key, statement=body.statement,
                                         entities=body.entities, scope=body.scope, valid_from=body.valid_from,
                                         source=body.source, valid_until=body.valid_until)
    await session.commit()
    return CanonicalClaimOut.model_validate(claim)


@router.patch("/{org_id}/canonical-claims/{claim_id}", response_model=CanonicalClaimOut)
async def patch_canonical_claim(org_id: uuid.UUID, claim_id: uuid.UUID, body: CanonicalClaimPatch,
                                session: SessionDep, actor: ActorDep):
    """Edit a claim, or soft-retire it with `{"status": "retired"}` (never deleted; a retired claim is final)."""
    await get_org(session, org_id)
    claim = await canonical.get_claim(session, org_id, claim_id)
    if claim is None:
        raise NotFound(f"canonical claim {claim_id} not found")
    claim = await canonical.update_claim(
        session, claim, actor, retire=body.status == "retired", retire_reason=body.retire_reason,
        statement=body.statement, entities=body.entities, scope=body.scope, valid_from=body.valid_from,
        valid_until=body.valid_until, source=body.source)
    await session.commit()
    return CanonicalClaimOut.model_validate(claim)
