"""DB/ORM constraints, immutability, fixture non-mutation, fresh-DB migration."""
from __future__ import annotations

import os
import subprocess
import sys
import uuid
from pathlib import Path

import pytest
from app.changeguard import canonical
from app.changeguard import service as cg
from app.models.changeguard import CanonicalClaim, ChangeCheck, ChangeSet, ImmutableChangeGuardError
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from tests.changeguard.conftest import protecting_experiment
from tests.changeguard.test_checks import inp, run


async def _one(session, org):
    check, sub = await run(session, org)
    return sub.change_set, check


async def test_change_set_and_check_are_immutable_in_the_orm(session, org):
    cs, check = await _one(session, org)
    cs_id, check_id = cs.id, check.id
    cs.reason = "edited"
    with pytest.raises(ImmutableChangeGuardError):
        await session.flush()
    await session.rollback()
    check = await session.get(ChangeCheck, check_id)
    check.decision = "ALLOW"
    check.findings = []
    with pytest.raises(ImmutableChangeGuardError):
        await session.flush()
    await session.rollback()
    for obj in (await session.get(ChangeSet, cs_id), await session.get(ChangeCheck, check_id)):
        await session.delete(obj)
        with pytest.raises(ImmutableChangeGuardError):
            await session.flush()
        await session.rollback()


async def test_database_constraints(session, org):
    cs, check = await _one(session, org)
    oid, cid = org.id, cs.id
    bad = [
        "insert into change_checks (id,change_set_id,org_id,decision,findings,semantic_check,guard_version,action_digest,"
        "experiment_context,experiment_refs,evaluated_at,created_at) values (gen_random_uuid(),:c,:o,'MAYBE','[]','ok','v','d','[]','[]',now(),now())",
        "insert into change_checks (id,change_set_id,org_id,decision,findings,semantic_check,guard_version,action_digest,"
        "experiment_context,experiment_refs,evaluated_at,created_at) values (gen_random_uuid(),:c,:o,'ALLOW','[]','passed','v','d','[]','[]',now(),now())",
        "insert into change_checks (id,change_set_id,org_id,decision,findings,semantic_check,guard_version,action_digest,"
        "experiment_context,experiment_refs,evaluated_at,created_at) values (gen_random_uuid(),gen_random_uuid(),:o,'ALLOW','[]','ok','v','d','[]','[]',now(),now())",
    ]
    for sql in bad:
        with pytest.raises(IntegrityError):
            await session.execute(text(sql), {"c": cid, "o": oid})
        await session.rollback()
    with pytest.raises(IntegrityError):  # unique (org, idempotency_key)
        await session.execute(text(
            "insert into change_sets (id,org_id,origin,agent_id,agent_name,source_mode,target_key,action_type,proposed_claims,"
            "claims_raw,prompt_cluster_ids,prompts,reason,idempotency_key,proposal_digest,created_at) select gen_random_uuid(),org_id,origin,agent_id,agent_name,"
            "source_mode,target_key,action_type,proposed_claims,claims_raw,prompt_cluster_ids,prompts,reason,idempotency_key,'x',now() from change_sets"))
    await session.rollback()
    with pytest.raises(IntegrityError):  # source_mode vocabulary
        await session.execute(text("update change_sets set source_mode='REAL'"))
    await session.rollback()
    with pytest.raises(IntegrityError):  # RESTRICT: an organization with change history cannot be deleted
        await session.execute(text("delete from organizations where id=:o"), {"o": oid})
    await session.rollback()


async def test_one_active_canonical_key_per_org_and_retire_is_final(session, org):
    org_id = org.id
    c = await canonical.create_claim(session, org.id, "alice", key="k1", statement="Seats are limited to 100.")
    cid = c.id
    await session.commit()
    with pytest.raises(Exception):  # noqa: B017
        await canonical.create_claim(session, org_id, "alice", key="k1", statement="Seats are limited to 200.")
    await session.rollback()
    row = await session.get(CanonicalClaim, cid)
    await session.delete(row)
    with pytest.raises(ImmutableChangeGuardError):
        await session.flush()
    await session.rollback()


async def test_checks_never_mutate_the_experiment(session, org):
    _, _, exp = await protecting_experiment(session, org)
    q = text("select md5(e::text) from experiments e where id=:i")
    before = (await session.execute(q, {"i": exp.id})).scalar()
    for i in range(3):
        await run(session, org, target_url="https://testco.example/enterprise/security", idempotency_key=f"k{i}")
    await session.rollback()
    assert (await session.execute(q, {"i": exp.id})).scalar() == before


def test_fresh_database_migrates_to_head_with_triggers_and_alembic_check_is_clean():
    import psycopg

    name = f"aeo_g1_mig_{os.getpid()}"
    admin = "postgresql://aeo:aeo@localhost:5432/postgres"
    try:
        c = psycopg.connect(admin, autocommit=True, connect_timeout=3)
    except Exception:
        pytest.skip("postgres unavailable")
    c.execute(f'drop database if exists "{name}" with (force)')
    c.execute(f'create database "{name}"')
    env = {**os.environ, "DATABASE_URL": f"postgresql+psycopg://aeo:aeo@localhost:5432/{name}"}
    backend = Path(__file__).resolve().parents[2]
    try:
        for args in (["upgrade", "head"], ["check"]):
            r = subprocess.run([sys.executable, "-m", "alembic", *args], cwd=backend, env=env, capture_output=True, text=True)
            assert r.returncode == 0, r.stderr[-2000:]
        with psycopg.connect(f"postgresql://aeo:aeo@localhost:5432/{name}", autocommit=True) as d:
            tables = {r[0] for r in d.execute("select tablename from pg_tables where schemaname='public'")}
            assert {"change_sets", "change_checks", "canonical_claims"} <= tables
            cols = {r[0] for r in d.execute("select column_name from information_schema.columns where table_name='approvals'")}
            assert "action_digest" in cols
            assert d.execute("select count(*) from pg_trigger where tgname like 'trg_change_%_append_only'").fetchone()[0] == 2
            org = uuid.uuid4()
            d.execute("insert into organizations (id,name,domain,competitor_domains,canonical_domains,personas,topics,created_at,updated_at)"
                      " values (%s,'o','o.example','[]','[]','[]','[]',now(),now())", (org,))
            d.execute("insert into change_sets (id,org_id,origin,agent_id,agent_name,source_mode,target_key,action_type,proposed_claims,"
                      "claims_raw,prompt_cluster_ids,prompts,reason,idempotency_key,proposal_digest,created_at) values "
                      "(%s,%s,'external','a','a','LIVE','','observe','[]','[]','[]','[]','','k','d',now())", (uuid.uuid4(), org))
            with pytest.raises(psycopg.errors.RestrictViolation):  # raw SQL is refused by the trigger too
                d.execute("update change_sets set reason='x'")
            with pytest.raises(psycopg.errors.RestrictViolation):
                d.execute("delete from change_sets")
    finally:
        c.execute(f'drop database if exists "{name}" with (force)')
        c.close()
