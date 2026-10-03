"""Approval lifecycle: propose -> pending -> approve / reject / modify / expire.

Rules enforced here (and by the ORM guard on `Approval`):
  * Only a HUMAN actor may approve, modify or reject. Model/system actors may only request or expire.
  * A decided approval is immutable.
  * `modified_change` is stored verbatim (deep-copied, JSON-checked, never normalised).
  * Nothing external may execute without an APPROVED/MODIFIED approval decided by a human
    (`require_executable_approval`); `observe` performs no external mutation and needs none.
"""
from __future__ import annotations

import copy
import json
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, ApprovalStatus
from app.models.interventions import Approval, Intervention

ACTOR_TYPES = ("human", "model", "system")
DECISION_STATUSES = (ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.MODIFIED)
GRANTED = (ApprovalStatus.APPROVED, ApprovalStatus.MODIFIED)
DEFAULT_TTL = timedelta(hours=72)


class ApprovalError(Exception):
    pass


class ApprovalImmutable(ApprovalError):
    """The approval was already decided."""


class HumanActorRequired(ApprovalError):
    """Approve/reject/modify must come from a human actor."""


class ApprovalExpired(ApprovalError):
    pass


class ExecutionNotAuthorized(ApprovalError):
    """No APPROVED/MODIFIED human approval exists for this intervention."""


def _now() -> datetime:
    return datetime.now(UTC)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def requires_approval(intervention: Intervention) -> bool:
    """`observe` is a monitoring decision with no external mutation."""
    return ActionType(intervention.action) != ActionType.OBSERVE


def decide(
    approval: Approval,
    status: ApprovalStatus | str,
    decided_by: str,
    note: str | None = None,
    modified_change: Mapping[str, Any] | None = None,
    *,
    actor_type: str = "human",
    now: datetime | None = None,
    action_digest: str | None = None,
) -> Approval:
    """Apply a decision to a pending approval in place (caller flushes/commits). `action_digest` (Change Guard) binds
    the decision to the exact change that was approved."""
    status = ApprovalStatus(status)
    now = now or _now()
    if status == ApprovalStatus.PENDING:
        raise ApprovalError("cannot decide back to pending")
    if approval.status != ApprovalStatus.PENDING:
        raise ApprovalImmutable(f"approval {approval.id} is already {ApprovalStatus(approval.status).value}")
    if actor_type not in ACTOR_TYPES:
        raise ApprovalError(f"actor_type must be one of {ACTOR_TYPES}")
    if not decided_by or not decided_by.strip():
        raise ApprovalError("decided_by is required")
    if status in DECISION_STATUSES and actor_type != "human":
        raise HumanActorRequired(f"{status.value} requires a human actor, got {actor_type!r}")
    if status == ApprovalStatus.EXPIRED and actor_type == "model":
        raise HumanActorRequired("a model actor cannot expire an approval")

    expires_at = _aware(approval.expires_at)
    if status in GRANTED and expires_at is not None and now > expires_at:
        raise ApprovalExpired(f"approval {approval.id} expired at {expires_at.isoformat()}")

    if status == ApprovalStatus.MODIFIED:
        if not modified_change:
            raise ApprovalError("modify requires a non-empty modified_change")
        try:
            json.dumps(modified_change)
        except (TypeError, ValueError) as exc:
            raise ApprovalError(f"modified_change must be JSON-serialisable: {exc}") from exc
        approval.modified_change = copy.deepcopy(dict(modified_change))
    elif modified_change is not None:
        raise ApprovalError("modified_change is only valid with status=modified")

    if action_digest:
        approval.action_digest = action_digest
    approval.status = status
    approval.decided_by = decided_by.strip()
    approval.decided_actor_type = actor_type
    approval.decided_at = now
    approval.note = note
    return approval


async def _audit(session: AsyncSession, actor_type: str, actor: str, approval: Approval, event: str,
                 metadata: dict) -> None:
    try:
        from app.core.audit import audit
    except ImportError:  # audit module not available
        return
    await audit(session, actor_type, actor, "approval", approval.id, event, metadata)


async def latest_approval(session: AsyncSession, intervention_id: uuid.UUID) -> Approval | None:
    rows = (
        await session.execute(
            select(Approval).where(Approval.intervention_id == intervention_id)
            .order_by(Approval.created_at.desc(), Approval.id.desc())
        )
    ).scalars().all()
    return rows[0] if rows else None


async def request_approval(
    session: AsyncSession,
    intervention: Intervention,
    requested_by: str = "system",
    *,
    actor_type: str = "system",
    ttl: timedelta | None = DEFAULT_TTL,
    now: datetime | None = None,
) -> Approval:
    """Open a PENDING approval. Returns the existing pending one (idempotent); refuses if already granted."""
    if actor_type not in ACTOR_TYPES:
        raise ApprovalError(f"actor_type must be one of {ACTOR_TYPES}")
    # Serialize every approval decision for this intervention on its row, so two simultaneous requests cannot both
    # open/decide an approval (the second sees the first's committed decision).
    await session.refresh(intervention, with_for_update=True)
    existing = await latest_approval(session, intervention.id)
    if existing is not None:
        if existing.status == ApprovalStatus.PENDING:
            return existing
        if existing.status in GRANTED:
            raise ApprovalError(f"intervention {intervention.id} already has a granted approval")
    now = now or _now()
    approval = Approval(
        id=uuid.uuid4(),
        intervention_id=intervention.id,
        status=ApprovalStatus.PENDING,
        requested_by=requested_by,
        requested_actor_type=actor_type,
        requested_change=copy.deepcopy(intervention.proposed_change or {}),
        expires_at=now + ttl if ttl else None,
        created_at=now,
    )
    try:
        async with session.begin_nested():  # partial unique index: one PENDING approval per intervention
            session.add(approval)
            await session.flush()
    except IntegrityError:
        existing = await latest_approval(session, intervention.id)
        if existing is not None and existing.status == ApprovalStatus.PENDING:
            return existing  # a concurrent request opened it first
        if existing is not None and existing.status in GRANTED:
            raise ApprovalError(f"intervention {intervention.id} already has a granted approval") from None
        raise
    await _audit(session, actor_type, requested_by, approval, "approval.requested",
                 {"intervention_id": str(intervention.id), "action": ActionType(intervention.action).value})
    return approval


async def decide_approval(
    session: AsyncSession,
    approval: Approval,
    status: ApprovalStatus | str,
    decided_by: str,
    note: str | None = None,
    modified_change: Mapping[str, Any] | None = None,
    *,
    actor_type: str = "human",
    now: datetime | None = None,
    action_digest: str | None = None,
) -> Approval:
    """`decide` + flush + audit event. Re-reads the row FOR UPDATE first so two simultaneous decisions serialize:
    the loser sees the committed decision and gets ApprovalImmutable instead of overwriting it."""
    await session.refresh(approval, with_for_update=True)
    decide(approval, status, decided_by, note, modified_change, actor_type=actor_type, now=now,
           action_digest=action_digest)
    await session.flush()
    await _audit(session, actor_type, decided_by, approval, f"approval.{ApprovalStatus(status).value}",
                 {"intervention_id": str(approval.intervention_id), "note": note,
                  "modified_change": approval.modified_change})
    return approval


async def expire_stale(session: AsyncSession, now: datetime | None = None) -> list[Approval]:
    """Expire pending approvals past `expires_at` (system actor)."""
    now = now or _now()
    pending = (
        await session.execute(select(Approval).where(Approval.status == ApprovalStatus.PENDING))
    ).scalars().all()
    expired = []
    for a in pending:
        exp = _aware(a.expires_at)
        if exp is not None and now > exp:
            await decide_approval(session, a, ApprovalStatus.EXPIRED, "system", "approval window elapsed",
                                  actor_type="system", now=now)
            expired.append(a)
    return expired


def effective_change(approval: Approval | None, intervention: Intervention) -> dict:
    """The change that may be executed: the human's modification if MODIFIED, else the proposal."""
    if approval is not None and approval.status == ApprovalStatus.MODIFIED and approval.modified_change:
        return copy.deepcopy(approval.modified_change)
    return copy.deepcopy(intervention.proposed_change or {})


async def require_executable_approval(session: AsyncSession, intervention: Intervention) -> Approval | None:
    """Gate for executors. Returns the granting approval (None for `observe`); raises otherwise.

    Also revalidates the Change Guard digest: the change about to be activated/executed/recorded must be the change that
    was approved (`ApprovalDigestMismatch`, 409 APPROVAL_DIGEST_MISMATCH). Every activation, execution and manual
    execution recording passes through here."""
    from app.changeguard.service import verify_binding

    if not requires_approval(intervention):
        await verify_binding(session, intervention, None)
        return None
    approval = await latest_approval(session, intervention.id)
    if approval is None:
        raise ExecutionNotAuthorized("no approval exists for this intervention")
    if approval.status not in GRANTED:
        raise ExecutionNotAuthorized(f"approval is {ApprovalStatus(approval.status).value}, not approved")
    if approval.decided_actor_type != "human" or not approval.decided_by:
        raise ExecutionNotAuthorized("approval was not decided by a human actor")
    await verify_binding(session, intervention, approval)
    return approval
