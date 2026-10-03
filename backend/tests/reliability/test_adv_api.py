"""Adversarial HTTP attacks: every request below tries to make AEO SRE report a success it did not earn.

Expected: a structured error {error: {code, type, message, details, request_id}}, no stack trace, and ZERO side
effects (no row written, no state moved).
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import ApprovalStatus, ExperimentStatus, IncidentState
from app.models.core import Incident, Job
from app.models.interventions import Approval, Execution, Experiment, Reward
from app.models.policy import PolicyVersion
from sqlalchemy import func, select

from tests import factories as f
from tests.reliability.helpers import CHANGE, executed_experiment, proposed_intervention

H = {"X-Actor": "alice@testco.example"}


def err(r):
    body = r.json()["error"]
    assert {"code", "type", "message", "details", "request_id"} <= set(body), body
    assert "Traceback" not in r.text and "File \"" not in r.text, "no stack trace may reach a client"
    return body


async def count(session, model) -> int:
    return (await session.execute(select(func.count()).select_from(model))).scalar()


async def reload(session, model, ident):
    return await session.get(model, ident, populate_existing=True)


# ---- verify / reward attacks ----------------------------------------------------------------------------------


@pytest.mark.parametrize("force", [False, True])
async def test_verify_before_eligible_is_refused_with_eligible_at_and_creates_no_job(app_client, session, org, force):
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(hours=1))  # window opens in 47h
    r = await app_client.post(f"/api/experiments/{exp.id}/verify", params={"force": str(force).lower()}, headers=H)
    assert r.status_code == 409, r.text
    e = err(r)
    assert e["code"] == "EXPERIMENT_NOT_VERIFIABLE_YET"
    eligible = datetime.fromisoformat(e["details"]["eligible_at"].replace("Z", "+00:00"))
    assert eligible == (now - timedelta(hours=1) + timedelta(hours=48)).replace(microsecond=eligible.microsecond) or abs(
        (eligible - (now + timedelta(hours=47))).total_seconds()) < 5
    assert await count(session, Job) == 0
    assert (await reload(session, Experiment, exp.id)).status == ExperimentStatus.AWAITING_VERIFICATION


async def test_verify_dry_run_and_terminal_states_are_refused(app_client, session, org):
    now = datetime.now(UTC)
    _, _, dry = await executed_experiment(session, org, executed_at=now - timedelta(days=3), dry_run=True)
    r = await app_client.post(f"/api/experiments/{dry.id}/verify", headers=H)
    assert r.status_code == 409 and err(r)["type"] == "conflict"
    for st in (ExperimentStatus.FAILED, ExperimentStatus.REJECTED, ExperimentStatus.PROPOSED):
        _, _, e = await executed_experiment(session, org, executed_at=now - timedelta(days=3), status=st)
        r = await app_client.post(f"/api/experiments/{e.id}/verify", headers=H)
        assert r.status_code == 409, (st, r.text)
    assert await count(session, Job) == 0 and await count(session, Reward) == 0


async def test_verify_unknown_and_malformed_ids_are_clean_errors(app_client):
    r = await app_client.post(f"/api/experiments/{uuid.uuid4()}/verify", headers=H)
    assert r.status_code == 404 and err(r)["code"] == "NOT_FOUND"
    r = await app_client.post("/api/experiments/not-an-id/verify", headers=H)
    assert r.status_code == 404


async def test_repeated_verify_after_window_returns_the_inflight_job_not_a_second_one(app_client, session, org, no_queue,
                                                                                     monkeypatch):
    now = datetime.now(UTC)
    _, _, exp = await executed_experiment(session, org, executed_at=now - timedelta(days=3))

    async def fake_enqueue(kind, payload, **kw):
        j = Job(kind=kind, status="queued", payload=payload)
        async with __import__("app.core.db", fromlist=["x"]).get_sessionmaker()() as s:
            s.add(j)
            await s.commit()
            return j.id

    import app.api.routes.experiments as routes

    monkeypatch.setattr(routes, "enqueue", fake_enqueue)
    a = await app_client.post(f"/api/experiments/{exp.id}/verify", headers=H)
    b = await app_client.post(f"/api/experiments/{exp.id}/verify", headers=H)
    assert a.status_code == b.status_code == 202, (a.text, b.text)
    assert a.json()["job"]["job_id"] == b.json()["job"]["job_id"]
    assert await count(session, Job) == 1


# ---- approval attacks -----------------------------------------------------------------------------------------


async def test_approve_twice_is_idempotent_no_second_approval_experiment_link_or_package(app_client, session, org):
    _, iv, exp = await proposed_intervention(session, org)
    first = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "go"}, headers=H)
    second = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "go"}, headers=H)
    assert first.status_code == 200 and second.status_code == 200, (first.text, second.text)
    assert second.json()["intervention"]["id"] == first.json()["intervention"]["id"]
    assert second.json()["incident_state"] == IncidentState.APPROVED.value
    assert await count(session, Approval) == 1
    assert await count(session, Execution) == 1, "one manual package, however many times approve is retried"
    e = await reload(session, Experiment, exp.id)
    assert e.status == ExperimentStatus.APPROVED and e.executed_at is None


async def test_reject_after_approve_and_approve_after_reject_are_refused_with_structured_conflict(app_client, session,
                                                                                                  org):
    _, iv, _ = await proposed_intervention(session, org)
    assert (await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)).status_code == 200
    r = await app_client.post(f"/api/interventions/{iv.id}/reject", json={}, headers=H)
    assert r.status_code == 409
    assert err(r)["code"] == "INVALID_STATE_TRANSITION"
    a = (await session.execute(select(Approval))).scalars().one()
    assert a.status == ApprovalStatus.APPROVED

    _, iv2, _ = await proposed_intervention(session, org)
    assert (await app_client.post(f"/api/interventions/{iv2.id}/reject", json={}, headers=H)).status_code == 200
    r = await app_client.post(f"/api/interventions/{iv2.id}/approve", headers=H)
    assert r.status_code == 409, "a rejected intervention can never be approved by retrying"


async def test_approve_invalid_inputs_have_no_side_effects(app_client, session, org):
    r = await app_client.post(f"/api/interventions/{uuid.uuid4()}/approve", headers=H)
    assert r.status_code == 404 and err(r)["code"] == "NOT_FOUND"
    r = await app_client.post("/api/interventions/zzz/approve", headers=H)
    assert r.status_code == 422 and err(r)["code"] == "VALIDATION_ERROR"
    _, iv, _ = await proposed_intervention(session, org, state=IncidentState.DETECTED)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 409 and err(r)["code"] == "INVALID_STATE_TRANSITION"
    r = await app_client.post(f"/api/interventions/{iv.id}/modify", json={"modified_change": {}}, headers=H)
    assert r.status_code == 422
    assert await count(session, Approval) == 0 and await count(session, Execution) == 0


async def test_approve_with_unknown_executor_is_refused_before_anything_is_decided(app_client, session, org):
    _, iv, _ = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"executor": "rm -rf"}, headers=H)
    assert r.status_code in (409, 422), r.text
    assert await count(session, Approval) == 0


async def test_approve_of_an_action_masked_by_the_operator_is_refused(app_client, session, org):
    """The operator disabled update_existing_page in policy settings: a stale proposal for it must not activate."""
    from app.models.core import Setting

    _, iv, exp = await proposed_intervention(session, org)
    session.add(Setting(key="policy", value={"allowed_actions": {"update_existing_page": False, "observe": True}}))
    await session.commit()
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 409, r.text
    assert err(r)["code"] == "ACTION_NOT_ELIGIBLE"
    assert await count(session, Execution) == 0
    assert (await reload(session, Experiment, exp.id)).status == ExperimentStatus.PROPOSED


async def test_observe_is_never_blockable_by_the_mask(app_client, session, org):
    from app.models.core import Setting

    from tests.reliability.helpers import OBSERVE

    _, iv, _ = await proposed_intervention(session, org, action="observe", change=OBSERVE)
    session.add(Setting(key="policy", value={"allowed_actions": {"observe": False}}))
    await session.commit()
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    assert r.status_code == 200, r.text


async def test_execute_before_approval_and_executed_before_approval_are_refused(app_client, session, org):
    _, iv, exp = await proposed_intervention(session, org)
    r = await app_client.post(f"/api/interventions/{iv.id}/execute", json={}, headers=H)
    assert r.status_code == 409
    r = await app_client.post(f"/api/interventions/{iv.id}/executed", json={"note": "did it"}, headers=H)
    assert r.status_code == 409
    e = await reload(session, Experiment, exp.id)
    assert e.executed_at is None and e.status == ExperimentStatus.PROPOSED


async def test_future_execution_time_cannot_open_a_window_in_the_future(app_client, session, org):
    _, iv, exp = await proposed_intervention(session, org)
    await app_client.post(f"/api/interventions/{iv.id}/approve", headers=H)
    future = (datetime.now(UTC) + timedelta(days=2)).isoformat()
    r = await app_client.post(f"/api/interventions/{iv.id}/executed", json={"executed_at": future}, headers=H)
    assert r.status_code == 422, r.text
    assert (await reload(session, Experiment, exp.id)).executed_at is None


# ---- API hygiene ----------------------------------------------------------------------------------------------


async def test_pagination_is_bounded_on_every_list_endpoint(app_client, session, org):
    inc = await f.make_incident(session, org)
    for path in ("/api/incidents", "/api/experiments", f"/api/incidents/{inc.id}/evidence"):
        assert (await app_client.get(path, params={"limit": 100000})).status_code == 422, path
        assert (await app_client.get(path, params={"offset": -1})).status_code == 422, path
        ok = await app_client.get(path, params={"limit": 1, "offset": 0})
        assert ok.status_code == 200 and "items" in ok.json(), path


async def test_oversized_request_body_is_rejected_413_before_parsing(app_client, session, org):
    _, iv, _ = await proposed_intervention(session, org)
    big = {"note": "x" * (2 * 1024 * 1024)}
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json=big, headers=H)
    assert r.status_code == 413, r.status_code
    assert err(r)["code"] == "PAYLOAD_TOO_LARGE"
    assert await count(session, Approval) == 0


async def test_unhandled_exception_never_leaks_internals(app_client, monkeypatch):
    import app.api.routes.incidents as routes

    async def boom(*a, **k):
        raise RuntimeError("postgresql://aeo:supersecret@db/aeo exploded at /srv/app/x.py")

    monkeypatch.setattr(routes.svc, "list_incidents", boom, raising=False)
    monkeypatch.setattr(routes.svc, "get_incident", boom)
    import httpx
    from app.api.main import app

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
                                 base_url="http://t") as c:
        r = await c.get(f"/api/incidents/{uuid.uuid4()}")
    assert r.status_code == 500
    assert "supersecret" not in r.text and "x.py" not in r.text
    assert err(r)["code"] == "INTERNAL_ERROR"


async def test_database_outage_is_a_503_with_a_code_not_a_500_trace(monkeypatch):
    import httpx
    from app.api.main import app
    from sqlalchemy.exc import OperationalError

    async def dead_session():
        raise OperationalError("select 1", {}, Exception("connection refused host=db password=hunter2"))
        yield  # pragma: no cover

    import app.api.routes.health as health_routes
    import app.core.db as appdb

    def dead_sessionmaker():
        raise OperationalError("select 1", {}, Exception("connection refused host=db password=hunter2"))

    monkeypatch.setattr(health_routes, "get_sessionmaker", dead_sessionmaker)
    app.dependency_overrides[appdb.get_session] = dead_session
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
            r = await c.get("/api/incidents")
            h = await c.get("/api/health")
    finally:
        app.dependency_overrides.pop(appdb.get_session, None)
    assert r.status_code == 503 and err(r)["code"] == "DATABASE_UNAVAILABLE"
    assert "hunter2" not in r.text
    assert h.status_code == 503 and h.json()["database"] != "healthy"


async def test_two_distinct_policy_versions_never_share_a_source_experiment(session, org):
    """DB-level backstop for 'policy update twice'."""
    from sqlalchemy.exc import IntegrityError

    _, _, exp = await executed_experiment(session, org)
    pv = (await session.execute(select(PolicyVersion))).scalars().first()
    session.add(PolicyVersion(version="vX.1", parent_id=pv.id, source_experiment_id=exp.id, n_updates=1,
                              state=pv.state, priors=pv.priors, algorithm=pv.algorithm))
    await session.commit()
    session.add(PolicyVersion(version="vX.2", parent_id=pv.id, source_experiment_id=exp.id, n_updates=1,
                              state=pv.state, priors=pv.priors, algorithm=pv.algorithm))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    assert (await session.execute(select(Incident))).scalars().all() is not None
