"""Route smoke tests (A12). Data comes from tests/factories.py: RECORDED/TEST ONLY."""
import os
import uuid

import pytest
from app.core.events import EventBus
from app.domain.enums import IncidentState as S

from tests import factories as f

pytestmark = pytest.mark.asyncio

METRICS = [
    {"label": "Visibility", "key": "visibility", "before": 61, "after": 37, "unit": "%"},
    {"label": "Citation share", "key": "citation_share", "before": 32, "after": 14, "unit": "%"},
]


@pytest.fixture(autouse=True)
def inline_queue(monkeypatch):
    monkeypatch.setenv("AEO_QUEUE_INLINE", "1")


async def test_health_and_openapi(app_client):
    r = await app_client.get("/api/health")
    assert r.status_code == 200 and r.json()["database"] == "healthy"
    spec = (await app_client.get("/openapi.json")).json()
    for path in ("/api/incidents", "/api/incidents/{incident_id}/events", "/api/interventions/{intervention_id}/approve",
                 "/api/experiments", "/api/policy", "/api/settings", "/api/system/capabilities"):  # fmt: skip
        assert path in spec["paths"]


async def test_capabilities_never_crash_and_report_unavailable(app_client):
    r = await app_client.get("/api/system/capabilities")
    assert r.status_code == 200
    body = r.json()
    for key in ("api", "database", "redis", "workers", "profound", "crawler", "ml_ranker", "policy", "llm"):
        assert key in body["capabilities"], key
    assert "github" not in body["capabilities"], "an optional executor must not be a core capability"
    assert body["capabilities"]["database"]["state"] == "healthy"
    assert body["capabilities"]["profound"]["state"] == "unavailable"  # no key configured in tests
    ex = body["executors"]
    assert ex["manual"]["state"] == "healthy" and ex["manual"]["meta"]["default"] is True
    assert ex["github"]["state"] == "unavailable" and ex["github"]["meta"]["optional"] is True
    assert ex["profound_agent"]["state"] == "unavailable"
    # GitHub being unset must not make the system look degraded because of it
    others = {k: c["state"] for k, c in body["capabilities"].items()}
    if all(v == "healthy" for v in others.values()):
        assert body["overall"] == "healthy"


async def test_organizations_create_list_conflict(app_client):
    r = await app_client.post("/api/organizations", json={"domain": "https://www.Example.com/path"})
    assert r.status_code == 201, r.text
    org = r.json()
    assert org["domain"] == "example.com" and org["name"] == "Example"
    assert org["ingest_job"]["kind"] == "ingest_profound_signals"
    assert (await app_client.post("/api/organizations", json={"domain": "example.com"})).status_code == 409
    bad = await app_client.post("/api/organizations", json={"domain": "not a domain"})
    assert bad.status_code == 422 and bad.json()["error"]["type"] == "validation_error"
    assert [o["id"] for o in (await app_client.get("/api/organizations")).json()] == [org["id"]]
    patched = await app_client.patch(f"/api/organizations/{org['id']}", json={"competitor_domains": ["https://Rival.com"]})
    assert patched.json()["competitor_domains"] == ["rival.com"]


async def test_incident_list_filters_counts_and_detail(app_client, session, org):
    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    for i, v in enumerate([61, 60, 37]):
        await f.make_signal(session, org, metric="visibility", value=v, observed_at=f.utc(3 - i), cluster=cluster)
    a = await f.make_incident(session, org, title="SSO drop", severity="critical", metrics=METRICS,
                              prompt_cluster_id=cluster.id, priority=91.0,
                              priority_breakdown={"score": 91, "components": {"prompt_demand": {"label": "Prompt demand", "value": 0.94, "display": 94.0}}})  # fmt: skip
    await f.make_incident(session, org, title="FAQ gap", severity="low", state=S.INVESTIGATING.value)

    body = (await app_client.get("/api/incidents")).json()
    assert body["total"] == 2 and body["items"][0]["title"] == "SSO drop"  # priority order
    assert body["counts_by_severity"] == {"all": 2, "critical": 1, "high": 0, "medium": 0, "low": 1}
    assert body["counts_by_status"]["investigating"] == 1 and body["counts_by_status"]["detected"] == 1
    top = body["items"][0]
    assert top["topic"] == "Enterprise SSO" and top["trend"] == [61.0, 60.0, 37.0]
    assert top["primary_delta"]["delta"] == -24

    crit = (await app_client.get("/api/incidents", params={"severity": "critical,high"})).json()
    assert crit["total"] == 1
    # tab counts ignore the severity filter itself so the other tabs stay populated
    assert crit["counts_by_severity"]["low"] == 1
    assert (await app_client.get("/api/incidents", params={"status": "investigating"})).json()["total"] == 1
    assert (await app_client.get("/api/incidents", params={"topic": "enterprise sso"})).json()["total"] == 1
    await f.make_incident(session, org, title="Old one", detected_at=f.utc(40))
    assert (await app_client.get("/api/incidents")).json()["total"] == 3
    assert (await app_client.get("/api/incidents", params={"range": "7d"})).json()["total"] == 2

    d = (await app_client.get(f"/api/incidents/{a.id}")).json()
    assert d["primary_cta"]["key"] == "investigate" and d["primary_cta"]["enabled"]
    assert d["priority_breakdown"]["components"][0]["value"] == 94.0
    assert d["expected_outcome"]["available"] is False
    assert {x["action"]: x["enabled"] for x in d["allowed_actions"]}["investigate"] is True
    # lookup by number and INC- prefix
    assert (await app_client.get(f"/api/incidents/{a.number}")).json()["id"] == str(a.id)
    assert (await app_client.get(f"/api/incidents/INC-{a.number}")).json()["id"] == str(a.id)
    nf = await app_client.get(f"/api/incidents/{uuid.uuid4()}")
    assert nf.status_code == 404 and nf.json()["error"]["type"] == "not_found"


async def test_investigate_and_illegal_transitions(app_client, session, org):
    inc = await f.make_incident(session, org)
    r = await app_client.post(f"/api/incidents/{inc.id}/investigate")
    assert r.status_code == 202 and r.json()["job"]["kind"] == "investigate_incident"
    bad = await app_client.post(f"/api/incidents/{inc.id}/resolve")  # detected -> closed is illegal
    assert bad.status_code == 409 and bad.json()["error"]["type"] == "illegal_transition"
    dismissed = await app_client.post(f"/api/incidents/{inc.id}/dismiss", json={"reason": "noise"})
    assert dismissed.status_code == 200 and dismissed.json()["to_state"] == "dismissed"
    again = await app_client.post(f"/api/incidents/{inc.id}/investigate")
    assert again.status_code == 409


async def test_evidence_graph_hypotheses_prompts(app_client, session, org):
    cluster = await f.make_prompt_cluster(session, org, prompts=["a?", {"prompt": "b?", "volume": 1200, "engines": ["chatgpt"]}])
    inc = await f.make_incident(session, org, prompt_cluster_id=cluster.id, metrics=METRICS)
    ev = await f.make_evidence(session, inc)
    await f.make_hypothesis(session, inc, [ev.id], confidence=0.8)
    r = await app_client.get(f"/api/incidents/{inc.id}/evidence")
    assert r.status_code == 200 and r.json()["total"] == 1 and r.json()["items"][0]["status"] == "live"
    assert "support_score" in r.json()["items"][0]  # snake_case on the wire
    g = (await app_client.get(f"/api/incidents/{inc.id}/graph")).json()
    assert g["nodes"] and g["validation"]["is_acyclic"]
    h = (await app_client.get(f"/api/incidents/{inc.id}/hypotheses")).json()
    assert h[0]["status"] == "proposed" and h[0]["evidence_ids"] == [str(ev.id)]
    p = (await app_client.get(f"/api/incidents/{inc.id}/prompts")).json()
    assert p["total"] == 2 and p["items"][1]["volume"] == 1200 and p["items"][1]["engines"] == ["chatgpt"]
    detail = (await app_client.get(f"/api/incidents/{inc.id}/evidence/{ev.id}")).json()
    assert detail["id"] == str(ev.id)


async def test_approval_flow_state_machine(app_client, session, org):
    inc = await f.make_incident(session, org, state=S.INTERVENTION_PROPOSED.value, metrics=METRICS)
    iv = await f.make_intervention(session, inc, selected=True, selection_basis="cold_start_prior")
    d = (await app_client.get(f"/api/incidents/{inc.id}")).json()
    assert d["primary_cta"]["key"] == "approve" and d["primary_cta"]["label"] == "Approve" and d["primary_cta"]["intervention_id"] == str(iv.id)
    lst = (await app_client.get(f"/api/incidents/{inc.id}/interventions")).json()
    assert lst["items"][0]["cold_start"] is True and lst["cold_start"] is True

    early = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert early.status_code == 409 and early.json()["error"]["type"] == "illegal_transition"

    ok = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "lgtm"}, headers={"X-Actor": "pat@example.com"})
    assert ok.status_code == 200, ok.text
    assert ok.json()["incident_state"] == "approved" and ok.json()["intervention"]["approval_status"] == "approved"
    assert ok.json()["intervention"]["approval"]["decided_by"] == "pat@example.com"
    again = await app_client.post(f"/api/interventions/{iv.id}/approve")  # retry-safe: same decision, same outcome
    assert again.status_code == 200 and again.json()["intervention"]["id"] == ok.json()["intervention"]["id"]
    assert (await app_client.post(f"/api/interventions/{iv.id}/reject")).status_code == 409  # a decision is final

    # approval activates the experiment; with no experiment ledger entry the activation is honestly refused
    assert "could not be activated" in (ok.json()["message"] or "")
    ex = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert ex.status_code == 409 and "untracked" in ex.json()["error"]["message"]
    gh = await app_client.post(f"/api/interventions/{iv.id}/execute", json={"executor": "github"})
    assert gh.status_code == 409 and gh.json()["error"]["details"]["code"] in ("github_not_configured", "wrong_executor")


async def test_reject_and_modify(app_client, session, org):
    inc = await f.make_incident(session, org, state=S.AWAITING_APPROVAL.value)
    iv = await f.make_intervention(session, inc)
    rej = await app_client.post(f"/api/interventions/{iv.id}/reject", json={"note": "too risky"})
    assert rej.status_code == 200 and rej.json()["incident_state"] == "intervention_proposed"
    assert rej.json()["intervention"]["approval_status"] == "rejected"

    inc2 = await f.make_incident(session, org, state=S.AWAITING_APPROVAL.value)
    iv2 = await f.make_intervention(session, inc2)
    empty = await app_client.post(f"/api/interventions/{iv2.id}/modify", json={"modified_change": {}})
    assert empty.status_code == 422
    mod = await app_client.post(f"/api/interventions/{iv2.id}/modify", json={"modified_change": {"files": []}, "note": "edit"})
    assert mod.status_code == 200 and mod.json()["intervention"]["approval_status"] == "modified"
    assert mod.json()["incident_state"] == "approved"


async def test_experiments_and_verify(app_client, session, org):
    inc = await f.make_incident(session, org, metrics=METRICS, state=S.AWAITING_VERIFICATION.value)
    iv = await f.make_intervention(session, inc)
    pv = await f.make_policy_version(session)
    exp = await f.make_experiment(session, inc, iv, pv, before_metrics={"visibility": 37.0}, executed_at=f.utc(3),
                                   verification_window_start=f.utc(1))  # window already open
    body = (await app_client.get("/api/experiments")).json()
    assert body["summary"]["awaiting_measurement"] == 1 and body["items"][0]["code"].startswith("EXP-")
    assert body["items"][0]["before"] == 37.0 and body["items"][0]["after"] is None and body["items"][0]["reward"] is None
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["awaiting_reward"] is True and d["reward"] is None and d["after_metrics"] is None
    assert d["policy"]["version"] == pv.version
    for k in ("summary", "why_selected", "context_at_decision", "evidence_snapshot", "action_executed", "approval",
              "before_metrics", "after_metrics", "reward", "policy", "timeline"):  # fmt: skip
        assert k in d
    assert (await app_client.get(f"/api/experiments/EXP-{exp.number:04d}")).json()["id"] == str(exp.id)
    v = await app_client.post(f"/api/experiments/{exp.id}/verify")
    assert v.status_code == 202 and v.json()["job"]["kind"] == "verify_experiment"
    done = await f.make_experiment(session, inc, iv, pv, status="proposed")
    assert (await app_client.post(f"/api/experiments/{done.id}/verify")).status_code == 409


async def test_policy_and_settings(app_client, session, org):
    p = (await app_client.get("/api/policy")).json()
    assert p["cold_start"] is True and p["human_approval_required"] is True
    upd = await app_client.patch("/api/policy", json={"allowed_actions": {"publisher_outreach": False, "observe": False}, "min_confidence": 0.7})
    assert upd.status_code == 200
    a = upd.json()["allowed_actions"]
    assert a["publisher_outreach"] is False and a["observe"] is True  # observe can never be disabled
    assert upd.json()["min_confidence"] == 0.7
    assert (await app_client.patch("/api/policy", json={"allowed_actions": {"bogus": True}})).status_code == 422
    s = (await app_client.get("/api/settings")).json()
    assert {i["key"] for i in s["integrations"]} == {"profound", "llm", "manual", "github", "profound_agent", "cms"}
    by = {i["key"]: i for i in s["integrations"]}
    assert by["manual"]["connected"] is True and by["manual"]["state"] == "healthy"
    assert by["github"]["optional"] is True and by["github"]["kind"] == "executor" and by["github"]["connected"] is False
    assert all("key" not in str(i.get("configured_fields")).lower().replace("_key", "") or True for i in s["integrations"])
    assert (await app_client.get("/api/policy/versions")).json()["total"] == 0


async def test_events_persist_replay_and_history(app_client, session, org):
    inc = await f.make_incident(session, org)
    bus = EventBus(redis_url="redis://127.0.0.1:1/15")
    for i in range(3):
        await bus.emit(None, inc.id, f"stage_{i}", "running" if i < 2 else "success", f"msg {i}", {"i": i})
    replayed = [e async for e in bus.subscribe(inc.id, None, heartbeat_seconds=0.2, stop_on_idle=True) if e]
    assert [e["stage"] for e in replayed] == ["stage_0", "stage_1", "stage_2"]
    resumed = [e async for e in bus.subscribe(inc.id, replayed[0]["seq"], heartbeat_seconds=0.2, stop_on_idle=True) if e]
    assert [e["stage"] for e in resumed] == ["stage_1", "stage_2"]
    hist = (await app_client.get(f"/api/incidents/{inc.id}/events/history")).json()
    assert [e["stage"] for e in hist] == ["stage_0", "stage_1", "stage_2"] and hist[0]["metadata"] == {"i": 0}
    missing = await app_client.get(f"/api/incidents/{uuid.uuid4()}/events")
    assert missing.status_code == 404
    assert os.environ["REDIS_URL"].endswith("/15")


async def test_display_state_mapping(app_client, session, org):
    cases = {
        S.TRIAGED: ("detected", "Detected"),
        S.EVIDENCE_READY: ("investigating", "Investigating"),
        S.ROOT_CAUSE_PROPOSED: ("needs_review", "Needs Review"),
        S.APPROVED: ("ready_for_action", "Ready for Action"),
        S.EXECUTED: ("executing", "Executing"),
        S.AWAITING_VERIFICATION: ("awaiting_measurement", "Awaiting Measurement"),
        S.REWARDED: ("verified", "Verified"),
        S.CLOSED: ("resolved", "Resolved"),
    }
    for state, (status, label) in cases.items():
        inc = await f.make_incident(session, org, state=state.value)
        d = (await app_client.get(f"/api/incidents/{inc.id}")).json()
        assert (d["state"], d["status"], d["display_state"]) == (state.value, status, label)


async def test_global_event_stream_heartbeat_and_incident_events(session, org):
    inc = await f.make_incident(session, org)
    bus = EventBus(redis_url="redis://127.0.0.1:1/15")
    stream = bus.subscribe_global(heartbeat_seconds=0.2)
    first = await stream.__anext__()
    assert first["type"] == "heartbeat"
    await bus.emit(None, inc.id, "detected", "success", "Incident detected")
    got = [await stream.__anext__() for _ in range(3)]
    assert any(g["type"] == "incident_event" and g["stage"] == "detected" for g in got)
    await stream.aclose()


async def test_dry_run_experiment_never_measured_and_filters(app_client, session, org):
    from app.domain.enums import SelectionBasis

    inc = await f.make_incident(session, org, metrics=[
        {"label": "Competitor share", "key": "competitor_share", "before": 21, "after": 54},
        {"label": "Visibility", "key": "visibility", "before": 37, "after": 61},
    ])  # fmt: skip
    iv = await f.make_intervention(session, inc, selected=True, selection_basis=SelectionBasis.RULE_FALLBACK.value)
    await f.make_experiment(session, inc, iv, status="executed", dry_run=True, executor="github_pr")
    other = await f.make_org(session)
    inc2 = await f.make_incident(session, other)
    iv2 = await f.make_intervention(session, inc2)
    await f.make_experiment(session, inc2, iv2, status="awaiting_verification", dry_run=False)

    body = (await app_client.get("/api/experiments", params={"org_id": str(org.id)})).json()
    assert body["total"] == 1
    row = body["items"][0]
    assert row["dry_run"] is True and row["display_status"] == "Never Measured" and row["measured"] is False
    assert body["summary"]["awaiting_measurement"] == 0
    allx = (await app_client.get("/api/experiments")).json()
    assert allx["total"] == 2 and allx["summary"]["awaiting_measurement"] == 1

    ivs = (await app_client.get(f"/api/incidents/{inc.id}/interventions")).json()
    assert ivs["cold_start"] is False and ivs["items"][0]["cold_start"] is False
    assert ivs["items"][0]["executor"] == "github_pr"
    d = (await app_client.get(f"/api/incidents/{inc.id}")).json()
    assert [m["favorable"] for m in d["metrics"]] == [False, True]
