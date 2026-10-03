"""DB integration: schema from empty DB, constraints, immutability guards."""
from __future__ import annotations

import uuid

import pytest
from app.core.db import Base
from app.domain.enums import ApprovalStatus
from sqlalchemy import create_engine, event, inspect, select, text
from sqlalchemy.exc import IntegrityError

from tests import factories as f
from tests.conftest import SYNC_URL, USE_PG

EXPECTED_TABLES = {
    "organizations", "prompt_clusters", "signals", "incidents", "incident_events", "jobs", "audit_events",
    "evidence", "evidence_edges", "hypotheses", "interventions", "approvals", "executions", "experiments",
    "observations", "rewards", "policy_versions", "policy_decisions",
}


def test_create_all_from_empty_database(tmp_path):
    """Base.metadata.create_all works on a brand-new empty database (no migrations needed for tests)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    Base.metadata.create_all(eng)
    tables = set(inspect(eng).get_table_names())
    missing = EXPECTED_TABLES - tables
    assert not missing, f"tables missing from metadata: {missing}"
    eng.dispose()


@pytest.mark.skipif(not USE_PG, reason="postgres only")
def test_postgres_schema_matches_metadata():
    eng = create_engine(SYNC_URL)
    with eng.connect() as c:
        pg_tables = {r[0] for r in c.execute(text("select tablename from pg_tables where schemaname='public'"))}
    eng.dispose()
    assert {t.name for t in Base.metadata.sorted_tables} <= pg_tables


async def test_org_domain_unique(session):
    await f.make_org(session, domain="dup.example")
    with pytest.raises(IntegrityError):
        await f.make_org(session, domain="dup.example")


async def test_incident_numbers_autoincrement_and_unique(session, org):
    a = await f.make_incident(session, org)
    b = await f.make_incident(session, org)
    assert a.number is not None and b.number == a.number + 1


@pytest.fixture
def fk_enforced(engine):
    """SQLite ignores FOREIGN KEYs unless PRAGMA foreign_keys=ON; Postgres always enforces them.
    Opt-in (not global): other unit tests build deliberately minimal SQLite rows without parents."""
    if USE_PG:
        yield
        return

    def _on(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    event.listen(engine.sync_engine, "connect", _on)  # NullPool: every new connection picks it up
    yield
    event.remove(engine.sync_engine, "connect", _on)


async def test_incident_requires_existing_org(session, fk_enforced):
    from app.models.core import Incident

    session.add(Incident(org_id=uuid.uuid4(), title="orphan"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_org_delete_cascades_to_incidents_signals(session, org, fk_enforced):
    from app.models.core import Incident, Signal

    await f.make_incident(session, org)
    await f.make_signal(session, org, value=1.0, observed_at=f.NOW)
    await session.delete(org)
    await session.commit()
    assert (await session.execute(select(Incident))).scalars().all() == []
    assert (await session.execute(select(Signal))).scalars().all() == []


async def test_one_reward_per_experiment(session, org):
    from app.models.interventions import Reward

    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    exp = await f.make_experiment(session, inc, iv)
    session.add(Reward(experiment_id=exp.id, components={}, total=0.1, weights={}))
    await session.commit()
    session.add(Reward(experiment_id=exp.id, components={}, total=0.2, weights={}))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_experiment_numbers_unique(session, org):
    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    a = await f.make_experiment(session, inc, iv)
    b = await f.make_experiment(session, inc, iv)
    assert a.number != b.number


async def test_invalid_enum_value_rejected(session, org):
    inc = await f.make_incident(session, org)
    with pytest.raises((LookupError, ValueError, IntegrityError, Exception)):
        await f.make_intervention(session, inc, action="invent_new_action")


# ---- immutability -----------------------------------------------------------------------------------------


async def test_policy_version_update_blocked(session):
    from app.models.policy import ImmutableVersionError

    pv = await f.make_policy_version(session)
    pv.n_updates = 99
    with pytest.raises(ImmutableVersionError):
        await session.commit()
    await session.rollback()


async def test_policy_version_delete_blocked(session):
    from app.models.policy import ImmutableVersionError

    pv = await f.make_policy_version(session)
    await session.delete(pv)
    with pytest.raises(ImmutableVersionError):
        await session.commit()
    await session.rollback()


async def test_policy_version_unique_version_string(session):
    from app.models.policy import PolicyVersion

    pv = await f.make_policy_version(session)
    session.add(PolicyVersion(version=pv.version, algorithm="linucb", state={}, priors={}))
    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.parametrize("final", [ApprovalStatus.APPROVED, ApprovalStatus.REJECTED, ApprovalStatus.MODIFIED])
async def test_decided_approval_cannot_be_changed(session, org, final):
    from app.models.interventions import ImmutableApprovalError

    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    ap = await f.make_approval(session, iv, status=final.value)
    ap.status = ApprovalStatus.APPROVED if final != ApprovalStatus.APPROVED else ApprovalStatus.REJECTED
    with pytest.raises(ImmutableApprovalError):
        await session.commit()
    await session.rollback()


async def test_decided_approval_note_edit_blocked(session, org):
    """Changing a field other than status on a decided approval must also be blocked."""
    from app.models.interventions import ImmutableApprovalError

    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    ap = await f.make_approval(session, iv, status="approved")
    ap.modified_change = {"files": [{"path": "evil.md", "diff": "+x"}]}
    with pytest.raises(ImmutableApprovalError):
        await session.commit()
    await session.rollback()


async def test_pending_approval_can_be_decided_once(session, org):
    inc = await f.make_incident(session, org)
    iv = await f.make_intervention(session, inc)
    ap = await f.make_approval(session, iv, status="pending")
    ap.status = ApprovalStatus.APPROVED
    ap.decided_by = "human@example.com"
    await session.commit()


@pytest.mark.asyncio
async def test_incident_numbers_unique_when_inserted_in_one_flush(session, org):
    """Regression: SQLite's max+1 counter fallback gave every row of one batched INSERT the same number."""
    from app.models.core import Incident

    base = await f.make_incident(session, org)
    rows = [Incident(**{c.name: getattr(base, c.name) for c in Incident.__table__.columns
                        if c.name not in ("id", "number", "created_at", "updated_at")}) for _ in range(3)]
    session.add_all(rows)
    await session.commit()
    nums = [r.number for r in rows] + [base.number]
    assert len(set(nums)) == 4 and all(n is not None for n in nums)
