"""Approval lifecycle (aiosqlite). Fixtures here are test-only."""
import uuid
from datetime import UTC, datetime, timedelta

import app.models
import pytest
import pytest_asyncio
from app.core.db import Base
from app.domain.enums import ActionType, ApprovalStatus, Risk
from app.models.interventions import Approval, ImmutableApprovalError, Intervention
from app.services import approvals as svc
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


async def _intervention(session: AsyncSession, action=ActionType.UPDATE_EXISTING_PAGE) -> Intervention:
    iv = Intervention(
        id=uuid.uuid4(), incident_id=uuid.uuid4(), action=action, title="Update SSO page",
        risk=Risk.LOW, proposed_change={"files": ["docs/sso.md"], "diff": "+ SAML supported"},
    )
    session.add(iv)
    await session.flush()
    return iv


async def test_request_creates_pending_with_snapshot(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv, requested_by="policy-v0.0.1", actor_type="model")
    assert a.status == ApprovalStatus.PENDING
    assert a.requested_change == iv.proposed_change and a.requested_change is not iv.proposed_change
    assert a.requested_actor_type == "model" and a.decided_by is None and a.expires_at is not None
    again = await svc.request_approval(session, iv)
    assert again.id == a.id  # idempotent while pending


async def test_human_approve_then_immutable(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "approved", "alice@example.com", "looks right")
    assert a.status == ApprovalStatus.APPROVED and a.decided_actor_type == "human" and a.decided_at
    for status in ("rejected", "approved", "modified", "expired"):
        with pytest.raises(svc.ApprovalImmutable):
            svc.decide(a, status, "bob", modified_change={"x": 1} if status == "modified" else None)
    # ORM-level guard: even bypassing the service, a decided approval cannot be changed
    a.note = "tamper"
    a.status = ApprovalStatus.REJECTED
    with pytest.raises(ImmutableApprovalError):
        await session.flush()
    await session.rollback()


async def test_model_and_system_cannot_decide(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv)
    for actor in ("model", "system"):
        for status in ("approved", "rejected", "modified"):
            with pytest.raises(svc.HumanActorRequired):
                svc.decide(a, status, "policy", modified_change={"x": 1}, actor_type=actor)
    assert a.status == ApprovalStatus.PENDING


async def test_modified_change_recorded_verbatim(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv)
    change = {"files": ["docs/sso.md", "docs/faq.md"], "diff": "+ edited by human", "nested": {"k": [1, 2, {"z": None}]}}
    await svc.decide_approval(session, a, ApprovalStatus.MODIFIED, "alice", "tweaked", change)
    await session.commit()
    session.expunge_all()
    got = await session.get(Approval, a.id)
    assert got.modified_change == change
    assert svc.effective_change(got, iv) == change
    assert iv.proposed_change != change  # original untouched


async def test_modify_requires_change_and_others_reject_it(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv)
    with pytest.raises(svc.ApprovalError):
        svc.decide(a, "modified", "alice")
    with pytest.raises(svc.ApprovalError):
        svc.decide(a, "approved", "alice", modified_change={"x": 1})
    with pytest.raises(svc.ApprovalError):
        svc.decide(a, "approved", "  ")
    with pytest.raises(svc.ApprovalError):
        svc.decide(a, "pending", "alice")
    assert a.status == ApprovalStatus.PENDING


async def test_expiry(session):
    iv = await _intervention(session)
    t0 = datetime(2026, 1, 1, tzinfo=UTC)
    a = await svc.request_approval(session, iv, ttl=timedelta(hours=1), now=t0)
    with pytest.raises(svc.ApprovalExpired):
        svc.decide(a, "approved", "alice", now=t0 + timedelta(hours=2))
    assert await svc.expire_stale(session, now=t0 + timedelta(minutes=30)) == []
    expired = await svc.expire_stale(session, now=t0 + timedelta(hours=2))
    assert [e.id for e in expired] == [a.id] and a.status == ApprovalStatus.EXPIRED
    assert a.decided_actor_type == "system"
    # an expired/rejected approval can be re-requested
    b = await svc.request_approval(session, iv)
    assert b.id != a.id and b.status == ApprovalStatus.PENDING


async def test_cannot_rerequest_after_grant(session):
    iv = await _intervention(session)
    a = await svc.request_approval(session, iv)
    await svc.decide_approval(session, a, "approved", "alice")
    with pytest.raises(svc.ApprovalError):
        await svc.request_approval(session, iv)


async def test_execution_gate(session):
    iv = await _intervention(session)
    with pytest.raises(svc.ExecutionNotAuthorized):
        await svc.require_executable_approval(session, iv)  # no approval at all
    a = await svc.request_approval(session, iv)
    with pytest.raises(svc.ExecutionNotAuthorized):
        await svc.require_executable_approval(session, iv)  # pending
    await svc.decide_approval(session, a, "rejected", "alice")
    with pytest.raises(svc.ExecutionNotAuthorized):
        await svc.require_executable_approval(session, iv)  # rejected
    b = await svc.request_approval(session, iv)
    await svc.decide_approval(session, b, "modified", "alice", modified_change={"diff": "x"})
    assert (await svc.require_executable_approval(session, iv)).id == b.id


async def test_gate_rejects_non_human_granted_row(session):
    iv = await _intervention(session)
    a = Approval(id=uuid.uuid4(), intervention_id=iv.id, status=ApprovalStatus.APPROVED, decided_by="bot",
                 decided_actor_type="model")
    session.add(a)
    await session.flush()
    with pytest.raises(svc.ExecutionNotAuthorized):
        await svc.require_executable_approval(session, iv)


async def test_observe_needs_no_approval(session):
    iv = await _intervention(session, ActionType.OBSERVE)
    assert not svc.requires_approval(iv)
    assert await svc.require_executable_approval(session, iv) is None


async def test_audit_events_written(session):
    from app.models.core import AuditEvent
    from sqlalchemy import select

    iv = await _intervention(session)
    a = await svc.request_approval(session, iv, "policy", actor_type="model")
    await svc.decide_approval(session, a, "approved", "alice")
    rows = (await session.execute(select(AuditEvent).order_by(AuditEvent.at))).scalars().all()
    assert {(r.event, r.actor_type) for r in rows} == {("approval.requested", "model"),
                                                       ("approval.approved", "human")}
