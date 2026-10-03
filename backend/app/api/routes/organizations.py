import re
import uuid
from urllib.parse import urlparse

import structlog
from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import ActorDep, SessionDep
from app.api.errors import ApiError, Conflict, NotFound
from app.core.audit import audit
from app.core.queue import QueueUnavailable, enqueue
from app.models.core import Incident, Job, Organization, Signal
from app.schemas.common import JobRef
from app.schemas.organizations import (
    OrganizationCreate,
    OrganizationCreated,
    OrganizationOut,
    OrganizationUpdate,
)

log = structlog.get_logger()
router = APIRouter(prefix="/api/organizations", tags=["organizations"])
_DOMAIN = re.compile(r"^(?=.{3,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def normalize_domain(raw: str) -> str:
    text = raw.strip().lower()
    host = urlparse(text if "://" in text else f"//{text}").hostname or ""
    host = host.removeprefix("www.")
    if not _DOMAIN.match(host):
        raise ValueError(f"{raw!r} is not a valid domain")
    return host


def default_name(domain: str) -> str:
    return domain.split(".")[0].replace("-", " ").title()


async def org_out(session, org: Organization) -> OrganizationOut:
    inc = (
        await session.execute(select(func.count()).select_from(Incident).where(Incident.org_id == org.id))
    ).scalar_one()
    sig = (
        await session.execute(
            select(func.count(), func.max(Signal.observed_at)).where(Signal.org_id == org.id)
        )
    ).one()
    out = OrganizationOut.model_validate(org)
    out.incident_count, out.signal_count, out.last_signal_at = inc, sig[0], sig[1]
    return out


async def get_org(session, org_id: uuid.UUID) -> Organization:
    org = await session.get(Organization, org_id)
    if org is None:
        raise NotFound(f"organization {org_id} not found")
    return org


@router.get("", response_model=list[OrganizationOut])
async def list_organizations(session: SessionDep):
    orgs = (await session.execute(select(Organization).order_by(Organization.created_at))).scalars().all()
    return [await org_out(session, o) for o in orgs]


@router.post("", response_model=OrganizationCreated, status_code=201)
async def create_organization(body: OrganizationCreate, session: SessionDep, actor: ActorDep):
    try:
        domain = normalize_domain(body.domain)
    except ValueError as exc:
        raise ApiError(str(exc), status_code=422, error_type="validation_error") from exc
    existing = (await session.execute(select(Organization).where(Organization.domain == domain))).scalar()
    if existing:
        raise Conflict(f"organization for {domain} already exists", {"organization_id": str(existing.id)})
    org = Organization(name=(body.name or default_name(domain)).strip(), domain=domain)
    session.add(org)
    await session.flush()
    await audit(session, "human", actor, "organization", org.id, "organization.created", {"domain": domain})
    await session.commit()
    try:
        job_id = await enqueue("ingest_profound_signals", {"org_id": str(org.id)})
        job = await session.get(Job, job_id)
        ref = JobRef(
            job_id=job_id,
            kind="ingest_profound_signals",
            status=job.status if job else "queued",
            error=job.error if job else None,
        )
    except QueueUnavailable as exc:
        log.warning("organization.ingest_enqueue_failed", org_id=str(org.id), error=str(exc))
        ref = JobRef(kind="ingest_profound_signals", status="unavailable", error=str(exc))
    out = await org_out(session, org)
    return OrganizationCreated(**out.model_dump(), ingest_job=ref)


@router.get("/{org_id}", response_model=OrganizationOut)
async def get_organization(org_id: uuid.UUID, session: SessionDep):
    return await org_out(session, await get_org(session, org_id))


@router.patch("/{org_id}", response_model=OrganizationOut)
async def update_organization(
    org_id: uuid.UUID, body: OrganizationUpdate, session: SessionDep, actor: ActorDep
):
    org = await get_org(session, org_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    for field, value in changes.items():
        if field.endswith("_domains"):
            try:
                value = sorted({normalize_domain(v) for v in value})
            except ValueError as exc:
                raise ApiError(str(exc), status_code=422, error_type="validation_error") from exc
        setattr(org, field, value)
    await audit(
        session, "human", actor, "organization", org.id, "organization.updated", {"fields": sorted(changes)}
    )
    await session.commit()
    return await org_out(session, org)
