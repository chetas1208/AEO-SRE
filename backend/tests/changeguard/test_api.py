"""POST/GET /api/change-checks and canonical-claims over HTTP (auth, idempotency, errors, OpenAPI)."""
from __future__ import annotations

import asyncio
import logging

from app.core.config import get_settings
from app.models.changeguard import ChangeSet
from sqlalchemy import func, select

from tests import factories as f
from tests.changeguard.conftest import PAGE, TOKEN, H, body, protecting_experiment

AUTH = {"Authorization": f"Bearer {TOKEN}"}


def code(r):
    return r.json()["error"]["code"]


# ---------------------------------------------------------------- auth
async def test_unset_token_is_503_for_post_only(app_client, org, monkeypatch):
    monkeypatch.delenv("CHANGE_GUARD_TOKEN", raising=False)
    get_settings.cache_clear()
    for r in (await app_client.post("/api/change-checks", json=body(org), headers=AUTH),
              await app_client.post("/api/change-checks", json=body(org))):
        assert r.status_code == 503 and code(r) == "CHANGE_GUARD_NOT_CONFIGURED", r.text
    assert (await app_client.get("/api/change-checks", params={"org_id": str(org.id)})).status_code == 200


async def test_missing_or_wrong_token_is_401_and_stores_nothing(app_client, org, token, session):
    for headers in ({}, {"Authorization": "Bearer nope"}, {"Authorization": TOKEN}, {"Authorization": "Basic abc"},
                    {"Authorization": "Bearer "}, {"Authorization": f"Bearer {TOKEN}x"}):
        r = await app_client.post("/api/change-checks", json=body(org), headers=headers)
        assert r.status_code == 401 and code(r) == "CHANGE_GUARD_UNAUTHORIZED", (headers, r.text)
    assert (await session.execute(select(func.count()).select_from(ChangeSet))).scalar() == 0
    assert (await app_client.get("/api/change-checks", params={"org_id": str(org.id)})).status_code == 200


async def test_auth_runs_before_body_validation(app_client, token):
    r = await app_client.post("/api/change-checks", json={"nonsense": True})
    assert r.status_code == 401


async def test_token_never_appears_in_responses_or_logs(app_client, org, token, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    ok = await app_client.post("/api/change-checks", json=body(org), headers=AUTH)
    bad = await app_client.post("/api/change-checks", json=body(org), headers={"Authorization": f"Bearer {TOKEN}zz"})
    out = capfd.readouterr()
    for blob in (ok.text, bad.text, str(ok.headers), caplog.text, out.out, out.err):
        assert TOKEN not in blob


def test_log_redaction_scrubs_the_configured_token(monkeypatch):
    from app.api.logging import redact_secrets

    monkeypatch.setenv("CHANGE_GUARD_TOKEN", TOKEN)
    get_settings.cache_clear()
    try:
        out = redact_secrets(None, "info", {"event": f"header was Bearer {TOKEN}", "authorization": f"Bearer {TOKEN}"})
        assert TOKEN not in str(out)
    finally:
        get_settings.cache_clear()


async def test_rate_limit_returns_429(app_client, org, token, monkeypatch):
    monkeypatch.setenv("CHANGE_GUARD_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    params = {"org_id": str(org.id)}
    codes = [(await app_client.get("/api/change-checks", params=params, headers=AUTH)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = await app_client.get("/api/change-checks", params=params, headers=AUTH)
    assert code(r) == "RATE_LIMITED" and r.json()["error"]["details"]["retry_after_seconds"] >= 1


# ---------------------------------------------------------------- create / replay / conflict
async def test_post_creates_then_replays_then_conflicts(app_client, org, token):
    b = body(org, idempotency_key="run-1")
    r1 = await app_client.post("/api/change-checks", json=b, headers=AUTH)
    assert r1.status_code == 201, r1.text
    d1 = r1.json()
    assert d1["decision"] == "ALLOW" and d1["replayed"] is False and d1["source_mode"] == "SIMULATED"
    assert d1["agent"] == {"id": "agent-7", "name": "Content Agent"}
    assert d1["semantic_check"] == "skipped_no_canonical_truth" and d1["guard_version"].startswith("change-guard/")
    assert len(d1["digest"]) == 64 and d1["target"] == "https://testco.example/pricing"
    r2 = await app_client.post("/api/change-checks", json=b, headers=AUTH)
    assert r2.status_code == 200 and r2.json()["replayed"] is True and r2.json()["id"] == d1["id"]
    assert r2.json()["digest"] == d1["digest"]
    r3 = await app_client.post("/api/change-checks", json={**b, "proposed_claims": ["something different."]}, headers=AUTH)
    assert r3.status_code == 409 and code(r3) == "CHANGE_CHECK_KEY_CONFLICT"
    assert r3.json()["error"]["details"]["change_set_id"] == d1["change_set_id"]


async def test_org_domain_alternative_and_unknown_org(app_client, org, token):
    b = body(org)
    del b["org_id"]
    b["org_domain"] = org.domain
    r = await app_client.post("/api/change-checks", json=b, headers=AUTH)
    assert r.status_code == 201 and r.json()["org_id"] == str(org.id)
    b["org_domain"] = "nobody.example"
    assert (await app_client.post("/api/change-checks", json=b, headers=AUTH)).status_code == 404
    b2 = body(org)
    del b2["org_id"]
    assert (await app_client.post("/api/change-checks", json=b2, headers=AUTH)).status_code == 422


async def test_validation_errors_are_typed_422(app_client, org, token):
    for bad in ({"action_type": "rewrite_everything"}, {"target_url": "javascript:alert(1)"},
                {"source_mode": "REAL"}, {"proposed_claims": ["x"] * 101}, {"proposed_claims": ["a" * 1001]},
                {"agent": {"id": ""}}, {"risk": "extreme"}):
        r = await app_client.post("/api/change-checks", json=body(org, **bad), headers=AUTH)
        assert r.status_code == 422, (bad, r.text)
        assert code(r) in ("VALIDATION_ERROR", "CHANGE_SET_INVALID")


async def test_oversized_body_is_413(app_client, org, token):
    r = await app_client.post("/api/change-checks", json=body(org, proposed_text="x" * 2_000_000), headers=AUTH)
    assert r.status_code == 413 and code(r) == "PAYLOAD_TOO_LARGE"


async def test_delay_over_http_returns_findings_and_eligible_after(app_client, org, token, session):
    _, _, exp = await protecting_experiment(session, org)
    r = await app_client.post("/api/change-checks", json=body(org, target_url=PAGE), headers=AUTH)
    d = r.json()
    assert r.status_code == 201 and d["decision"] == "DELAY" and d["experiment_codes"] == [exp.code]
    f1 = d["findings"][0]
    assert f1["type"] == "active_experiment_contamination" and f1["decision"] == "DELAY" and f1["check"] == 1
    assert f1["references"]["experiment_code"] == exp.code and f1["details"]["target_overlap_pct"] == 100.0
    assert d["eligible_after"].endswith("Z") or "+" in d["eligible_after"]
    from datetime import datetime

    assert datetime.fromisoformat(d["eligible_after"].replace("Z", "+00:00")) == exp.verification_window_start


# ---------------------------------------------------------------- GET
async def test_list_filters_and_pagination(app_client, org, token, session):
    await protecting_experiment(session, org)
    other = await f.make_org(session)
    for i in range(3):
        await app_client.post("/api/change-checks", json=body(org, target_url=PAGE, agent={"id": f"a{i}"}), headers=AUTH)
    await app_client.post("/api/change-checks", json=body(org, target_url="https://testco.example/x"), headers=AUTH)
    await app_client.post("/api/change-checks", json=body(other), headers=AUTH)

    oid = str(org.id)
    r = await app_client.get("/api/change-checks", params={"org_id": oid}, headers=AUTH)
    assert r.json()["total"] == 4 and r.json()["limit"] == 50
    assert (await app_client.get("/api/change-checks", params={"org_id": oid, "decision": "delay"}, headers=AUTH)).json()["total"] == 3
    t = await app_client.get("/api/change-checks", params={"org_id": oid, "target": PAGE + "/"}, headers=AUTH)
    assert t.json()["total"] == 3
    e = await app_client.get("/api/change-checks", params={"org_id": oid, "experiment_code": "exp-0001"}, headers=AUTH)
    assert e.json()["total"] == 3
    p = await app_client.get("/api/change-checks", params={"org_id": oid, "limit": 2, "offset": 2}, headers=AUTH)
    assert len(p.json()["items"]) == 2 and p.json()["total"] == 4
    assert (await app_client.get("/api/change-checks", params={"org_id": oid, "limit": 0}, headers=AUTH)).status_code == 422
    first = r.json()["items"][0]
    one = await app_client.get(f"/api/change-checks/{first['id']}", headers=AUTH)
    assert one.status_code == 200 and one.json()["id"] == first["id"]
    import uuid

    assert (await app_client.get(f"/api/change-checks/{uuid.uuid4()}", headers=AUTH)).status_code == 404


# ---------------------------------------------------------------- concurrency
async def test_two_identical_requests_at_once_store_one_change_set_and_one_replay(app_client, org, token, session):
    b = body(org, idempotency_key="race-1")
    rs = await asyncio.gather(*[app_client.post("/api/change-checks", json=b, headers=AUTH) for _ in range(2)])
    assert sorted(r.status_code for r in rs) == [200, 201], [r.text for r in rs]
    assert len({r.json()["id"] for r in rs}) == 1
    assert sorted(r.json()["replayed"] for r in rs) == [False, True]
    assert (await session.execute(select(func.count()).select_from(ChangeSet))).scalar() == 1


# ---------------------------------------------------------------- experiment protection + OpenAPI
async def test_experiment_detail_protection_block(app_client, org, token, session):
    _, _, exp = await protecting_experiment(session, org)
    r = await app_client.post("/api/change-checks", json=body(org, target_url=PAGE), headers=AUTH)
    assert r.json()["decision"] == "DELAY"
    d = (await app_client.get(f"/api/experiments/{exp.code}")).json()["protection"]
    assert d["protected"] is True and d["checks_blocked_count"] == 1
    assert d["until"].startswith(exp.verification_window_start.strftime("%Y-%m-%dT%H:%M:%S"))
    assert d["until_basis"] == "window_start" and PAGE in d["targets"]
    rc = d["recent_checks"][0]
    assert rc["decision"] == "DELAY" and rc["source_mode"] == "SIMULATED" and rc["agent_id"] == "agent-7"
    assert rc["reasons"] and rc["eligible_after"]


async def test_finished_experiment_is_not_protected(app_client, org, session):
    _, _, exp = await protecting_experiment(session, org, status="verified")
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()["protection"]
    assert d["protected"] is False and d["until"] is None and d["checks_blocked_count"] == 0


async def test_openapi_documents_the_routes_and_schemas(app_client):
    spec = (await app_client.get("/openapi.json")).json()
    paths = spec["paths"]
    assert {"post", "get"} <= set(paths["/api/change-checks"]) and "get" in paths["/api/change-checks/{check_id}"]
    assert {"get", "post"} <= set(paths["/api/organizations/{org_id}/canonical-claims"])
    assert "patch" in paths["/api/organizations/{org_id}/canonical-claims/{claim_id}"]
    assert "post" in paths["/api/interventions/{intervention_id}/change-check"]
    schemas = spec["components"]["schemas"]
    for name in ("ChangeSetIn", "ChangeCheckOut", "FindingOut", "Protection", "ChangeGuardVerdict", "CanonicalClaimOut"):
        assert name in schemas, name
    assert "protection" in schemas["ExperimentDetail"]["properties"]
    assert "change_guard" in schemas["InterventionOut"]["properties"]
    assert set(schemas["ChangeCheckOut"]["properties"]["decision"]["enum"]) == {
        "ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK"}
    for p in ("/api/change-checks",):
        assert "409" in paths[p]["post"]["responses"] and "503" in paths[p]["post"]["responses"]


# ---------------------------------------------------------------- canonical claims
async def test_canonical_claims_crud_audit_and_soft_retire(app_client, org, session):
    url = f"/api/organizations/{org.id}/canonical-claims"
    c = {"key": "saml-plan", "statement": "SAML SSO is only available on the Enterprise plan.",
         "entities": ["SAML"], "scope": "*", "source": "pricing.md"}
    r = await app_client.post(url, json=c, headers=H)
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert r.json()["status"] == "active" and r.json()["created_by"] == H["X-Actor"]
    assert (await app_client.post(url, json=c, headers=H)).status_code == 422  # active key is unique
    for actor in ("system", "agent:content", "model"):
        bad = await app_client.post(url, json={**c, "key": "other"}, headers={"X-Actor": actor})
        assert bad.status_code == 422 and code(bad) == "CANONICAL_CLAIM_INVALID"
    p = await app_client.patch(f"{url}/{cid}", json={"statement": "SAML SSO is available on Enterprise only."}, headers=H)
    assert p.status_code == 200 and p.json()["updated_by"] == H["X-Actor"]
    assert (await app_client.get(url)).json()["total"] == 1
    ret = await app_client.patch(f"{url}/{cid}", json={"status": "retired", "retire_reason": "plans changed"}, headers=H)
    assert ret.json()["status"] == "retired" and ret.json()["retired_by"] == H["X-Actor"] and ret.json()["retired_at"]
    assert (await app_client.patch(f"{url}/{cid}", json={"statement": "Edit after retire."}, headers=H)).status_code == 422
    again = await app_client.post(url, json=c, headers=H)  # key is free again once the old claim is retired
    assert again.status_code == 201
    listing = (await app_client.get(url)).json()
    assert listing["total"] == 2
    assert (await app_client.get(url, params={"status": "retired"})).json()["total"] == 1
    from app.models.core import AuditEvent

    events = (await session.execute(select(AuditEvent.event, AuditEvent.actor).where(
        AuditEvent.event == "canonical_claim.changed"))).all()
    assert len(events) == 4 and {a for _, a in events} == {H["X-Actor"]}
    import uuid

    assert (await app_client.patch(f"{url}/{uuid.uuid4()}", json={"statement": "x y z"}, headers=H)).status_code == 404
    assert (await app_client.get(f"/api/organizations/{uuid.uuid4()}/canonical-claims")).status_code == 404


async def test_change_check_audit_events_are_written(app_client, org, token, session):
    from app.models.core import AuditEvent

    r = await app_client.post("/api/change-checks", json=body(org), headers=AUTH)
    names = sorted((await session.execute(select(AuditEvent.event).where(
        AuditEvent.entity_id == r.json()["id"]))).scalars().all())
    assert names == ["change_check.created", "change_check.decided"]
