"""Muse connector: auth, org binding, tools, Read-never-persists, feedback idempotency, rate limit, manifest."""
from __future__ import annotations

import logging

import pytest
from app.core.config import get_settings
from app.domain.enums import IncidentCategory
from app.integrations.muse import perception
from app.integrations.muse.manifest import build_manifest
from app.integrations.muse.tools import TOOLS
from app.models.changeguard import CanonicalClaim, ChangeCheck, ChangeSet
from app.models.core import AuditEvent, Incident
from app.models.interventions import Approval, Experiment, Intervention

from tests import factories as f
from tests.muse.conftest import AUTH, KEY, SIM, claim, count

pytestmark = pytest.mark.usefixtures("muse_env")
CHECK = "/muse/tools/check-change"
VERIFY = "/muse/tools/verify-claim"
INTENT = "/muse/tools/check-intent"
GAPS = "/muse/tools/list-discovery-gaps"
FEEDBACK = "/muse/tools/record-feedback"
DOMAIN_TABLES = (ChangeSet, ChangeCheck, CanonicalClaim, Incident, Experiment, Intervention, Approval)


def code(r):
    return r.json()["error"]["code"]


# ---------------------------------------------------------------- auth + org binding
async def test_unset_key_is_503_not_configured(app_client, monkeypatch):
    monkeypatch.delenv("MUSE_CONNECTOR_API_KEY", raising=False)
    get_settings.cache_clear()
    for h in (AUTH, {}):
        r = await app_client.post(VERIFY, json={"claim": "x y z"}, headers=h)
        assert r.status_code == 503 and code(r) == "MUSE_CONNECTOR_NOT_CONFIGURED"


async def test_auth_matrix(app_client):
    for h in ({}, {"Authorization": "Bearer nope"}, {"Authorization": KEY}, {"Authorization": "Basic abc"},
              {"Authorization": "Bearer "}, {"Authorization": f"Bearer {KEY}x"}):
        r = await app_client.post(VERIFY, json={"claim": "x y z"}, headers=h)
        assert r.status_code == 401 and code(r) == "MUSE_UNAUTHORIZED", h
        assert set(r.json()["error"]) == {"code", "type", "message", "details", "request_id"}
    assert (await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers=AUTH)).status_code == 200


async def test_auth_runs_before_validation_and_org_resolution(app_client):
    r = await app_client.post(VERIFY, json={"junk": 1})
    assert r.status_code == 401


async def test_org_not_bound_is_503(app_client, monkeypatch):
    monkeypatch.delenv("MUSE_ORGANIZATION_ID", raising=False)
    get_settings.cache_clear()
    r = await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers=AUTH)
    assert r.status_code == 503 and code(r) == "MUSE_ORGANIZATION_NOT_CONFIGURED"


async def test_bound_by_domain_and_never_sees_other_orgs(app_client, session, org, monkeypatch):
    other = await f.make_org(session)
    await claim(session, other, key="other-only", statement="Widgets cost 10 dollars per month.")
    monkeypatch.delenv("MUSE_ORGANIZATION_ID", raising=False)
    monkeypatch.setenv("MUSE_ORGANIZATION_DOMAIN", org.domain)
    get_settings.cache_clear()
    r = await app_client.post(VERIFY, json={"claim": "Widgets cost 99 dollars per month."}, headers=AUTH)
    assert r.status_code == 200
    assert r.json()["status"] == "unknown" and r.json()["canonical_claims_considered"] == 0  # other org's truth is invisible
    assert "org_id" not in r.json() and "org_id" not in (await app_client.get("/muse/manifest")).text


async def test_body_cannot_select_another_org(app_client, session):
    other = await f.make_org(session)
    r = await app_client.post(VERIFY, json={"claim": "SSO is offered", "org_id": str(other.id)}, headers=AUTH)
    assert r.status_code == 422 and code(r) == "INVALID_REQUEST"


# ---------------------------------------------------------------- verify_claim
async def test_verify_empty_canonical_truth_is_unknown_not_a_pass(app_client, session):
    r = await app_client.post(VERIFY, json={"claim": "SAML SSO is available on the Pro plan."}, headers=AUTH)
    j = r.json()
    assert r.status_code == 200 and j["status"] == "unknown"
    assert "no canonical truth" in j["reasons"][0] and j["source"] == "muse" and j["source_mode"] == "LIVE"


async def test_verify_contradicted_with_basis(app_client, session, org):
    await claim(session, org)
    r = await app_client.post(VERIFY, json={"claim": "SAML SSO is available on the Pro plan."}, headers=AUTH)
    j = r.json()
    assert j["status"] == "contradicted", j
    assert j["basis"][0]["key"] == "saml-plan" and j["reasons"]


async def test_verify_supported(app_client, session, org):
    await claim(session, org)
    r = await app_client.post(VERIFY, json={"claim": "SAML SSO is available only on the Enterprise plan."}, headers=AUTH)
    assert r.json()["status"] == "supported", r.json()


async def test_verify_unaddressed_claim_is_unknown(app_client, session, org):
    await claim(session, org)
    r = await app_client.post(VERIFY, json={"claim": "The office dog is called Biscuit."}, headers=AUTH)
    assert r.json()["status"] == "unknown"


async def test_verify_degraded_when_comparison_fails(app_client, session, org, monkeypatch):
    await claim(session, org)

    def boom(*a, **k):
        raise RuntimeError("engine down")

    monkeypatch.setattr("app.integrations.muse.tools._run_g2", boom)
    r = await app_client.post(VERIFY, json={"claim": "SAML SSO is available on the Pro plan."}, headers=AUTH)
    j = r.json()
    assert r.status_code == 200 and j["status"] == "degraded" and "no result is claimed" in j["reasons"][0]


async def test_verify_validation_errors_are_typed(app_client):
    r = await app_client.post(VERIFY, json={"claim": "x"}, headers=AUTH)
    assert r.status_code == 422 and code(r) == "INVALID_REQUEST"
    r = await app_client.post(VERIFY, content=b"{not json", headers=AUTH)
    assert r.status_code == 422 and code(r) == "INVALID_REQUEST"
    r = await app_client.post(VERIFY, json=["a"], headers=AUTH)
    assert r.status_code == 422
    r = await app_client.post(VERIFY, json={"claim": "SSO is offered", "scope_url": "not a url"}, headers=AUTH)
    assert r.status_code == 422


async def test_body_too_large_is_413(app_client):
    r = await app_client.post(VERIFY, json={"claim": "a" * 70000}, headers=AUTH)
    assert r.status_code == 413 and code(r) == "PAYLOAD_TOO_LARGE"


# ---------------------------------------------------------------- check_change
async def test_check_change_blocks_on_canonical_conflict_and_persists_nothing(app_client, session, org):
    await claim(session, org)
    before = await count(session, *DOMAIN_TABLES)
    r = await app_client.post(CHECK, headers=AUTH, json={
        "target_url": "https://testco.example/pricing", "proposed_claims": ["SAML SSO is available on the Pro plan."]})
    j = r.json()
    assert r.status_code == 200 and j["decision"] == "BLOCK" and j["persisted"] is False and j["advisory"] is True
    assert any(x["type"] == "canonical_conflict" for x in j["findings"])
    assert await count(session, *DOMAIN_TABLES) == before


async def test_check_change_empty_truth_is_allow_with_visible_skip(app_client, session):
    r = await app_client.post(CHECK, headers=AUTH, json={"proposed_claims": ["SAML SSO is available on the Pro plan."]})
    j = r.json()
    assert j["decision"] == "ALLOW" and j["semantic_check"] == "skipped_no_canonical_truth"
    assert any(x["type"] == "canonical_truth_unavailable" for x in j["findings"])


async def test_check_change_reports_degraded_semantic_check(app_client, session, org, monkeypatch):
    await claim(session, org)

    def boom(*a, **k):
        raise RuntimeError("down")

    monkeypatch.setattr("app.changeguard.semantic._run_g2", boom)
    r = await app_client.post(CHECK, headers=AUTH, json={"proposed_claims": ["SAML SSO is available on the Pro plan."]})
    j = r.json()
    assert j["semantic_check"] == "degraded" and any(x["type"] == "semantic_check_degraded" for x in j["findings"])


async def test_check_change_rejects_bad_action_and_url(app_client):
    assert (await app_client.post(CHECK, headers=AUTH, json={"action_type": "delete_everything"})).status_code == 422
    r = await app_client.post(CHECK, headers=AUTH, json={"target_url": "ftp://x"})
    assert r.status_code == 422


# ---------------------------------------------------------------- check_intent
INTENT_BODY = {"intent": "compare enterprise SSO", "requirements": ["SAML SSO is available only on the Enterprise plan.",
                                                                   "SAML SSO is available on the Pro plan."],
               "constraints": {"plan": "enterprise"}, "ttl_seconds": 600}


async def test_check_intent_reports_truth_and_perception_unavailable(app_client, session, org):
    await claim(session, org)
    r = await app_client.post(INTENT, json=INTENT_BODY, headers=AUTH)
    j = r.json()
    assert r.status_code == 200 and j["ai_perception"] == "unavailable" and j["ai_perception_reason"]
    by = {i["ref"]: i for i in j["items"]}
    assert by["requirement:0"]["product_truth"] == "satisfied" and by["requirement:1"]["product_truth"] == "contradicted"
    assert all(i["ai_perception"] == "unavailable" and i["discovery_gap"] is False for i in j["items"])
    assert j["discovery_gaps"] == 0 and j["expires_in_seconds"] == 600


class FakePerception:
    async def perceive(self, org_id, statements):
        return {s: perception.PerceptionVerdict("contradicts", ["engine says no"], "SIMULATED") for s in statements}


class BrokenPerception:
    async def perceive(self, org_id, statements):
        raise RuntimeError("profound down")


async def test_check_intent_flags_discovery_gap_only_when_truth_satisfied_and_ai_disagrees(app_client, session, org):
    await claim(session, org)
    perception.set_perception_provider(FakePerception())
    j = (await app_client.post(INTENT, json=INTENT_BODY, headers=AUTH)).json()
    by = {i["ref"]: i for i in j["items"]}
    assert j["ai_perception"] == "available"
    assert by["requirement:0"]["discovery_gap"] is True and by["requirement:0"]["perception_evidence"] == ["engine says no"]
    assert by["requirement:1"]["discovery_gap"] is False  # truth contradicted: not a gap
    assert j["discovery_gaps"] == 1


async def test_check_intent_provider_failure_is_unavailable_never_fabricated(app_client, session, org):
    await claim(session, org)
    perception.set_perception_provider(BrokenPerception())
    j = (await app_client.post(INTENT, json=INTENT_BODY, headers=AUTH)).json()
    assert j["ai_perception"] == "unavailable" and "RuntimeError" in j["ai_perception_reason"]
    assert all(i["ai_perception"] == "unavailable" for i in j["items"])


async def test_check_intent_empty_truth_is_unknown(app_client):
    j = (await app_client.post(INTENT, json=INTENT_BODY, headers=AUTH)).json()
    assert {i["product_truth"] for i in j["items"]} == {"unknown"}


async def test_check_intent_rejects_identity_and_expired(app_client):
    r = await app_client.post(INTENT, headers=AUTH, json={**INTENT_BODY, "constraints": {"email": "a@b.co"}})
    assert r.status_code == 422 and code(r) == "IDENTITY_FIELD_REJECTED" and "a@b.co" not in r.text
    r = await app_client.post(INTENT, headers=AUTH, json={**INTENT_BODY, "expires_at": "2020-01-01T00:00:00Z"})
    assert r.status_code == 422 and code(r) == "INTENT_EXPIRED"
    r = await app_client.post(INTENT, headers=AUTH, json={**INTENT_BODY, "surprise": 1})
    assert r.status_code == 422 and code(r) == "INVALID_INTENT_ENVELOPE"


# ---------------------------------------------------------------- list_discovery_gaps
async def _gap(session, org, **kw):
    return await f.make_incident(session, org, category=IncidentCategory.FACTUAL_CONFLICT, context={"discovery_gap": {
        "canonical_key": "saml-plan", "canonical_statement": "SAML SSO is Enterprise only.",
        "perceived_claim": "SAML SSO is on every plan", "kind": "conflict", "relation": "CONFLICTING",
        "confidence": 0.9, "engines": ["chatgpt"], "occurrence": 3, "evidence_grade": "factcheck",
        "source_mode": "SIMULATED", "origin": "factcheck_claims"}}, **kw)


async def test_list_gaps_empty_is_valid(app_client):
    j = (await app_client.post(GAPS, json={}, headers=AUTH)).json()
    assert j["items"] == [] and j["total"] == 0 and j["note"]


async def test_list_gaps_paginates_scopes_org_and_skips_plain_incidents(app_client, session, org):
    other = await f.make_org(session)
    for _ in range(3):
        await _gap(session, org)
    await _gap(session, other)
    await f.make_incident(session, org, category=IncidentCategory.FACTUAL_CONFLICT)  # not a discovery gap
    await _gap(session, org, state="DISMISSED")
    j = (await app_client.post(GAPS, json={"limit": 2}, headers=AUTH)).json()
    assert j["total"] == 3 and len(j["items"]) == 2 and j["items"][0]["canonical_key"] == "saml-plan"
    assert j["items"][0]["gap_source_mode"] == "SIMULATED" and j["source_mode"] == "LIVE"
    assert len((await app_client.post(GAPS, json={"limit": 2, "offset": 2}, headers=AUTH)).json()["items"]) == 1
    assert (await app_client.post(GAPS, json={"include_dismissed": True}, headers=AUTH)).json()["total"] == 4
    assert (await app_client.post(GAPS, json={"limit": 500}, headers=AUTH)).status_code == 422


# ---------------------------------------------------------------- Read tools never persist
async def test_read_tools_leave_every_domain_table_unchanged(app_client, session, org):
    await claim(session, org)
    await _gap(session, org)
    before = await count(session, *DOMAIN_TABLES)
    for path, body in ((CHECK, {"proposed_claims": ["SAML SSO is available on the Pro plan."]}),
                       (VERIFY, {"claim": "SAML SSO is available on the Pro plan."}), (INTENT, INTENT_BODY), (GAPS, {})):
        assert (await app_client.post(path, json=body, headers=AUTH)).status_code == 200, path
    assert await count(session, *DOMAIN_TABLES) == before


# ---------------------------------------------------------------- record_feedback
async def _check_id(session, org):
    from app.changeguard import service as cg

    sub = await cg.submit(session, cg.ChangeInput(
        org_id=org.id, agent_id="a", action_type="update_existing_page", target_url="https://testco.example/p",
        proposed_claims=["Widgets ship in 2 days."], source_mode="SIMULATED"))
    await session.commit()
    return str(sub.check.id)


async def test_feedback_idempotent_conflict_and_scoped(app_client, session, org):
    cid = await _check_id(session, org)
    body = {"target_type": "change_check", "target_id": cid, "rating": "helpful", "idempotency_key": "fb-0001-aaaa"}
    before = await count(session, *DOMAIN_TABLES)
    r1 = await app_client.post(FEEDBACK, json=body, headers=SIM)
    r2 = await app_client.post(FEEDBACK, json=body, headers=SIM)
    assert r1.status_code == r2.status_code == 200
    assert r1.json()["replayed"] is False and r2.json()["replayed"] is True
    assert r1.json()["feedback_id"] == r2.json()["feedback_id"] and r1.json()["source_mode"] == "SIMULATED"
    assert await count(session, *DOMAIN_TABLES) == before  # feedback touches no domain table
    from sqlalchemy import func, select
    n = (await session.execute(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.event == "muse.feedback.recorded"))).scalar_one()
    assert n == 1
    r3 = await app_client.post(FEEDBACK, json={**body, "rating": "incorrect"}, headers=AUTH)
    assert r3.status_code == 409 and code(r3) == "IDEMPOTENCY_KEY_CONFLICT"
    r4 = await app_client.post(FEEDBACK, json={**body, "target_id": "00000000-0000-0000-0000-000000000001",
                                               "idempotency_key": "fb-0002-bbbb"}, headers=AUTH)
    assert r4.status_code == 404 and code(r4) == "NOT_FOUND"


async def test_feedback_on_other_orgs_record_is_404(app_client, session, org):
    other = await f.make_org(session)
    cid = await _check_id(session, other)
    r = await app_client.post(FEEDBACK, headers=AUTH, json={
        "target_type": "change_check", "target_id": cid, "rating": "helpful", "idempotency_key": "fb-0003-cccc"})
    assert r.status_code == 404


async def test_feedback_on_discovery_gap_and_validation(app_client, session, org):
    g = await _gap(session, org)
    ok = await app_client.post(FEEDBACK, headers=AUTH, json={
        "target_type": "discovery_gap", "target_id": str(g.id), "rating": "not_helpful", "comment": "wrong engine",
        "idempotency_key": "fb-0004-dddd"})
    assert ok.status_code == 200
    bad = await app_client.post(FEEDBACK, headers=AUTH, json={
        "target_type": "discovery_gap", "target_id": str(g.id), "rating": "great", "idempotency_key": "x"})
    assert bad.status_code == 422


# ---------------------------------------------------------------- audit
async def test_one_audit_event_per_call_without_content(app_client, session):
    from sqlalchemy import select
    await app_client.post(VERIFY, json={"claim": "My secret phrase zebra"}, headers=AUTH)
    await app_client.post(VERIFY, json={"claim": "x"}, headers=AUTH)  # 422 is audited too
    await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers={"Authorization": "Bearer bad"})  # not audited
    rows = (await session.execute(select(AuditEvent).where(AuditEvent.event == "muse.tool.called"))).scalars().all()
    assert len(rows) == 2
    assert all(r.actor_type == "system" and r.actor == "muse-connector" for r in rows)
    assert "zebra" not in str([r.metadata_ for r in rows]) and {r.metadata_["status"] for r in rows} == {200, 422}


# ---------------------------------------------------------------- source mode
async def test_source_mode_rules(app_client, monkeypatch):
    body = {"claim": "SSO is offered"}
    assert (await app_client.post(VERIFY, json=body, headers=AUTH)).json()["source_mode"] == "LIVE"
    assert (await app_client.post(VERIFY, json=body, headers=SIM)).json()["source_mode"] == "SIMULATED"
    assert (await app_client.post(VERIFY, json=body, headers={**AUTH, "X-Muse-Source-Mode": "bogus"})).status_code == 422
    monkeypatch.setenv("ENVIRONMENT", "production")
    get_settings.cache_clear()
    assert (await app_client.post(VERIFY, json=body, headers=SIM)).json()["source_mode"] == "LIVE"  # ignored
    monkeypatch.setenv("MUSE_ALLOW_SIMULATED", "true")
    get_settings.cache_clear()
    assert (await app_client.post(VERIFY, json=body, headers=SIM)).json()["source_mode"] == "SIMULATED"


# ---------------------------------------------------------------- rate limit
async def test_rate_limit_per_key_and_failed_auth_throttle(app_client, monkeypatch):
    monkeypatch.setenv("MUSE_RATE_LIMIT_PER_MINUTE", "3")
    get_settings.cache_clear()
    codes = [(await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers=AUTH)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]
    r = await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers=AUTH)
    assert code(r) == "RATE_LIMITED" and int(r.headers["retry-after"]) >= 1
    assert r.json()["error"]["details"]["retry_after_seconds"] >= 1
    bad = [(await app_client.post(VERIFY, json={}, headers={"Authorization": "Bearer no"})).status_code for _ in range(5)]
    assert bad == [401, 401, 401, 429, 429]


# ---------------------------------------------------------------- manifest + routes
async def test_manifest_equals_tool_schemas_and_is_public(app_client):
    r = await app_client.get("/muse/manifest")  # no auth
    assert r.status_code == 200
    m = r.json()
    assert m == build_manifest(get_settings()) or m["tools"] == build_manifest(get_settings())["tools"]
    assert [t["name"] for t in m["tools"]] == [t.name for t in TOOLS]
    for t, spec in zip(m["tools"], TOOLS, strict=True):
        assert t["input_schema"] == spec.input_model.model_json_schema()
        assert t["output_schema"] == spec.output_model.model_json_schema()
        assert t["classification"] in ("read", "write") and t["path"] == spec.path
    cls = {t["name"]: t["classification"] for t in m["tools"]}
    assert cls == {"check_change": "read", "verify_claim": "read", "check_intent": "read",
                   "list_discovery_gaps": "read", "record_feedback": "write"}
    assert m["auth"]["scheme"] == "bearer" and KEY not in r.text


def test_no_approve_apply_or_sensitive_routes_exist():
    from app.api.main import app

    paths = {p: v for p, v in app.openapi()["paths"].items() if p.startswith("/muse")}
    assert set(paths) == {"/muse/manifest", *(t.path for t in TOOLS)}
    for p in paths:
        assert not any(w in p for w in ("approve", "apply", "execute", "retire", "delete", "canonical"))
    assert all(set(v) == {"post"} for p, v in paths.items() if p.startswith("/muse/tools"))
    assert set(paths["/muse/manifest"]) == {"get"}


# ---------------------------------------------------------------- secrets
async def test_key_never_in_responses_or_logs(app_client, caplog, capfd):
    caplog.set_level(logging.DEBUG)
    ok = await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers=AUTH)
    bad = await app_client.post(VERIFY, json={"claim": "SSO is offered"}, headers={"Authorization": f"Bearer {KEY}zz"})
    out = capfd.readouterr()
    for blob in (ok.text, bad.text, str(ok.headers), str(bad.headers), caplog.text, out.out, out.err):
        assert KEY not in blob


def test_log_redaction_scrubs_the_connector_key():
    from app.api.logging import redact_secrets

    out = redact_secrets(None, "info", {"event": f"header was Bearer {KEY}", "authorization": f"Bearer {KEY}"})
    assert KEY not in str(out)
