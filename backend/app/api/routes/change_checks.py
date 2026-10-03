"""Change Guard HTTP API: agents post intended ChangeSets, one decision comes back.

Auth for POST: `Authorization: Bearer <CHANGE_GUARD_TOKEN>` (constant-time compare). With the env var unset, POST
answers 503 CHANGE_GUARD_NOT_CONFIGURED (secure by default). GET list is read-only from PostgreSQL for the control-plane
UI: requires `org_id` and does not use the agent bearer token (external agents still POST with the token).
UI-originated evaluations never use POST here; they call `app.changeguard.service` directly (interventions routes).
"""
from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from collections import deque
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Header, Request, Response
from sqlalchemy import String, cast, func, select

from app.api.deps import BusDep, PageDep, SessionDep
from app.api.errors import NotFound
from app.changeguard import service as cg
from app.changeguard.targets import try_normalize
from app.core.config import get_settings
from app.domain.enums import StepStatus
from app.domain.errors import ChangeGuardNotConfigured, ChangeGuardUnauthorized, RateLimited
from app.models.changeguard import ChangeCheck, ChangeSet
from app.schemas.changeguard import AgentRef, ChangeCheckList, ChangeCheckOut, ChangeSetIn

log = structlog.get_logger()

_hits: dict[str, deque[float]] = {}
RATE_WINDOW_S = 60.0


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _rate_limit(request: Request) -> None:
    """Sliding window per client address (in-process: one API process; put a proxy limiter in front for more)."""
    limit = int(get_settings().change_guard_rate_limit_per_minute)
    if limit <= 0:
        return
    now = time.monotonic()
    q = _hits.setdefault(_client_key(request), deque())
    while q and now - q[0] > RATE_WINDOW_S:
        q.popleft()
    if len(q) >= limit:
        raise RateLimited("too many change checks; slow down", {"limit_per_minute": limit,
                                                                "retry_after_seconds": int(RATE_WINDOW_S - (now - q[0])) + 1})
    q.append(now)


def _digest(s: str) -> bytes:
    return hashlib.sha256(s.encode("utf-8")).digest()


async def rate_limit(request: Request) -> None:
    _rate_limit(request)


async def require_token(request: Request, authorization: Annotated[str | None, Header()] = None) -> None:
    configured = get_settings().change_guard_token
    if not configured:
        raise ChangeGuardNotConfigured(
            "Change Guard is not configured: set CHANGE_GUARD_TOKEN to enable POST /api/change-checks")
    await rate_limit(request)
    scheme, _, supplied = (authorization or "").partition(" ")
    ok = scheme.lower() == "bearer" and bool(supplied.strip())
    # compare fixed-length digests in constant time; always compare, even when the header is malformed
    same = hmac.compare_digest(_digest(supplied.strip() if ok else ""), _digest(configured))
    if not (ok and same):
        raise ChangeGuardUnauthorized("missing or invalid bearer token")


router = APIRouter(prefix="/api/change-checks", tags=["change-guard"])


def check_out(cs: ChangeSet, check: ChangeCheck, *, replayed: bool = False) -> ChangeCheckOut:
    return ChangeCheckOut(
        id=check.id, change_set_id=cs.id, org_id=cs.org_id, decision=check.decision, findings=check.findings or [],
        eligible_after=check.eligible_after, merged_proposal=check.merged_proposal,
        semantic_check=check.semantic_check, guard_version=check.guard_version, digest=check.action_digest,
        proposal_digest=cs.proposal_digest, replayed=replayed, agent=AgentRef(id=cs.agent_id, name=cs.agent_name),
        source_mode=cs.source_mode, origin=cs.origin, target_url=cs.target_url, target=cs.target_key[7:] or None
        if cs.target_key else None, action_type=cs.action_type, profound_run_id=cs.profound_run_id,
        idempotency_key=cs.idempotency_key,
        experiment_codes=[r for r in (check.experiment_refs or []) if str(r).startswith("EXP-")],
        evaluated_at=check.evaluated_at, created_at=check.created_at)


async def publish_events(bus, check: ChangeCheck, cs: ChangeSet) -> None:
    """Small SSE events on the incident(s) of the experiments the check touched (best effort, after commit)."""
    incidents = {f["references"]["incident_id"] for f in (check.findings or [])
                 if (f.get("references") or {}).get("incident_id")}
    for inc in sorted(incidents):
        try:
            await bus.emit(None, uuid.UUID(inc), "change_check.decided",
                           StepStatus.SUCCESS if check.decision == "ALLOW" else StepStatus.WARNING,
                           f"Change check {check.decision}: {cs.agent_name or cs.agent_id}"
                           + (" (SIMULATED)" if cs.source_mode == "SIMULATED" else ""),
                           {"check_id": str(check.id), "decision": check.decision, "agent_id": cs.agent_id,
                            "source_mode": cs.source_mode, "target": cs.target_url,
                            "eligible_after": cg.iso(check.eligible_after)})
        except Exception as exc:  # noqa: BLE001
            log.warning("change_check.event_failed", error=type(exc).__name__)


@router.post("", response_model=ChangeCheckOut, status_code=201, dependencies=[Depends(require_token)])
async def create_change_check(body: ChangeSetIn, response: Response, session: SessionDep, bus: BusDep):
    """Create + evaluate a ChangeSet. 201 = new decision; 200 + `replayed: true` = same idempotency key and digest
    (the stored decision). Same key with a different proposal -> 409 CHANGE_CHECK_KEY_CONFLICT."""
    org = await cg.resolve_org(session, body.org_id, body.org_domain)
    inp = cg.ChangeInput(
        org_id=org.id, agent_id=body.agent.id, agent_name=body.agent.name, profound_run_id=body.profound_run_id,
        source_mode=body.source_mode, target_url=body.target_url, action_type=body.action_type,
        proposed_claims=body.proposed_claims, proposed_text=body.proposed_text, proposed_diff=body.proposed_diff,
        reason=body.reason, expected_kpi=body.expected_kpi, risk=body.risk, reversible=body.reversible,
        idempotency_key=body.idempotency_key, prompt_cluster_ids=[str(c) for c in body.prompt_cluster_ids],
        prompts=body.prompts, recheck=body.recheck)
    sub = await cg.submit(session, inp)
    out = check_out(sub.change_set, sub.check, replayed=sub.replayed)
    if sub.replayed:
        response.status_code = 200
        await session.rollback()
        return out
    await session.commit()
    await publish_events(bus, sub.check, sub.change_set)
    return out


@router.get("", response_model=ChangeCheckList, dependencies=[Depends(rate_limit)])
async def list_change_checks(
    session: SessionDep, page: PageDep, org_id: uuid.UUID, decision: str | None = None,
    target: str | None = None, experiment_code: str | None = None, source_mode: str | None = None,
    origin: str | None = None,
):
    base = select(ChangeSet, ChangeCheck).join(ChangeCheck, ChangeCheck.change_set_id == ChangeSet.id)
    if org_id:
        base = base.where(ChangeSet.org_id == org_id)
    if decision:
        base = base.where(ChangeCheck.decision == decision.upper())
    if source_mode:
        base = base.where(ChangeSet.source_mode == source_mode.upper())
    if origin:
        base = base.where(ChangeSet.origin == origin)
    if target:
        n = try_normalize(target)
        base = base.where(ChangeSet.target_key.startswith(f"target:{(n or target).lower()}"))
    if experiment_code:
        base = base.where(cast(ChangeCheck.experiment_refs, String).contains(f'"{experiment_code.upper()}"'))
    total = (await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (await session.execute(base.order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc())
                                  .limit(page.limit).offset(page.offset))).all()
    return ChangeCheckList(items=[check_out(cs, ck) for cs, ck in rows], total=total, limit=page.limit,
                           offset=page.offset)


@router.get("/{check_id}", response_model=ChangeCheckOut)
async def get_change_check(check_id: uuid.UUID, session: SessionDep):
    row = (await session.execute(select(ChangeSet, ChangeCheck).join(ChangeCheck, ChangeCheck.change_set_id == ChangeSet.id)
                                 .where(ChangeCheck.id == check_id))).first()
    if row is None:
        raise NotFound(f"change check {check_id} not found")
    return check_out(*row)
