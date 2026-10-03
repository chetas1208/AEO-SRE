"""FastAPI contract tests: health, capabilities, organizations, incident list/filters/detail, transitions."""
from __future__ import annotations

import uuid

import pytest
from app.domain.enums import IncidentState, Severity

from tests import factories as f


async def test_health_ok_with_db(app_client):
    r = await app_client.get("/api/health")
    assert r.status_code in (200,)
    body = r.json()
    assert body["database"] == "healthy"
    assert body["status"] in ("ok", "degraded")  # redis intentionally unreachable in tests -> degraded
    assert body["redis"] in ("degraded", "unavailable", "healthy")


async def test_openapi_published_and_covers_contract(app_client):
    r = await app_client.get("/openapi.json")
    assert r.status_code == 200
    paths = r.json()["paths"]
    for p in ("/api/health", "/api/system/capabilities", "/api/organizations", "/api/incidents",
              "/api/incidents/detect", "/api/incidents/{incident_id}", "/api/incidents/{incident_id}/events",
              "/api/incidents/{incident_id}/evidence", "/api/incidents/{incident_id}/graph",
              "/api/incidents/{incident_id}/hypotheses", "/api/incidents/{incident_id}/prompts",
              "/api/incidents/{incident_id}/interventions", "/api/interventions/{intervention_id}/approve",
              "/api/interventions/{intervention_id}/reject", "/api/interventions/{intervention_id}/modify",
              "/api/interventions/{intervention_id}/execute", "/api/experiments", "/api/experiments/{experiment_id}",
              "/api/experiments/{experiment_id}/verify", "/api/policy", "/api/policy/versions", "/api/settings"):
        assert p in paths, f"contract path missing: {p}"


async def test_capabilities_never_claim_unconfigured_integrations_healthy(app_client):
    r = await app_client.get("/api/system/capabilities")
    assert r.status_code == 200
    text = r.text.lower()
    body = r.json()
    assert body, "capabilities must be reported"
    # Profound / GitHub / LLM have no credentials in the test env: must not be reported as healthy.
    items = body.get("capabilities") or body.get("items") or body
    flat = items if isinstance(items, list) else list(items.values()) if isinstance(items, dict) else []
    for cap in flat:
        if not isinstance(cap, dict):
            continue
        name = str(cap.get("name") or cap.get("id") or "").lower()
        if any(k in name for k in ("profound", "github", "llm", "model")):
            assert cap.get("state") != "healthy", f"{name} reported healthy without credentials: {cap}"
    assert "healthy" in text or "unavailable" in text or "degraded" in text


async def test_create_org_validates_domain(app_client):
    r = await app_client.post("/api/organizations", json={"domain": "not a domain"})
    assert r.status_code == 422
    assert r.json()["error"]["type"] == "validation_error"


async def test_create_org_and_duplicate_conflict(app_client, monkeypatch):
    monkeypatch.setenv("AEO_QUEUE_INLINE", "")
    r = await app_client.post("/api/organizations", json={"domain": "https://www.Acme-Test.example/"})
    # queue is unreachable (redis down) -> org still created, ingest job reported unavailable, never faked
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["domain"] == "acme-test.example"
    assert body["ingest_job"]["status"] in ("unavailable", "queued", "failed")
    dup = await app_client.post("/api/organizations", json={"domain": "acme-test.example"})
    assert dup.status_code == 409


async def test_list_organizations(app_client, org):
    r = await app_client.get("/api/organizations")
    assert r.status_code == 200
    assert [o["domain"] for o in r.json()] == [org.domain]


# ---- incidents -----------------------------------------------------------------------------------------


@pytest.fixture
async def seeded(session, org):
    a = await f.make_incident(session, org, title="Alpha SSO visibility drop", severity=Severity.CRITICAL, priority=90)
    b = await f.make_incident(session, org, title="Beta pricing citation loss", severity=Severity.LOW, priority=20,
                              state=IncidentState.INVESTIGATING)
    c = await f.make_incident(session, org, title="Gamma factual conflict", severity=Severity.HIGH, priority=60,
                              state=IncidentState.DISMISSED)
    return a, b, c


async def test_empty_incident_list_is_empty_not_fake(app_client):
    r = await app_client.get("/api/incidents")
    assert r.status_code == 200
    body = r.json()
    assert body["items"] == [] and body["total"] == 0


async def test_incident_list_sorted_by_priority(app_client, seeded):
    r = await app_client.get("/api/incidents")
    titles = [i["title"] for i in r.json()["items"]]
    assert titles[0] == "Alpha SSO visibility drop"
    pri = [i["priority"] for i in r.json()["items"]]
    assert pri == sorted(pri, reverse=True)


async def test_incident_filter_severity(app_client, seeded):
    r = await app_client.get("/api/incidents", params={"severity": "critical"})
    assert [i["title"] for i in r.json()["items"]] == ["Alpha SSO visibility drop"]
    r = await app_client.get("/api/incidents", params=[("severity", "critical"), ("severity", "low")])
    assert {i["title"] for i in r.json()["items"]} == {"Alpha SSO visibility drop", "Beta pricing citation loss"}
    r = await app_client.get("/api/incidents", params={"severity": "critical,low"})
    assert r.json()["total"] == 2


async def test_incident_filter_status_by_raw_state(app_client, seeded):
    r = await app_client.get("/api/incidents", params={"status": "investigating"})
    assert [i["title"] for i in r.json()["items"]] == ["Beta pricing citation loss"]


async def test_incident_search_and_pagination(app_client, seeded):
    r = await app_client.get("/api/incidents", params={"q": "pricing"})
    assert r.json()["total"] == 1
    r = await app_client.get("/api/incidents", params={"limit": 1, "offset": 1})
    assert len(r.json()["items"]) == 1
    assert (await app_client.get("/api/incidents", params={"limit": 0})).status_code == 422
    assert (await app_client.get("/api/incidents", params={"sort": "bogus"})).status_code == 422


async def test_incident_detail_by_uuid_and_number(app_client, seeded):
    a, _, _ = seeded
    by_uuid = await app_client.get(f"/api/incidents/{a.id}")
    assert by_uuid.status_code == 200
    d = by_uuid.json()
    assert d["title"] == a.title
    assert d["state"] == IncidentState.DETECTED.value
    by_num = await app_client.get(f"/api/incidents/{a.number}")
    assert by_num.status_code == 200 and by_num.json()["id"] == str(a.id)


async def test_incident_not_found_is_404_typed(app_client):
    r = await app_client.get(f"/api/incidents/{uuid.uuid4()}")
    assert r.status_code == 404
    assert r.json()["error"]["type"] == "not_found"
    r = await app_client.get("/api/incidents/not-an-id")
    assert r.status_code in (404, 422)


async def test_detail_does_not_invent_evidence_hypotheses_or_interventions(app_client, seeded):
    a, _, _ = seeded
    for sub in ("evidence", "hypotheses"):
        r = await app_client.get(f"/api/incidents/{a.id}/{sub}")
        assert r.status_code == 200, r.text
    ev = (await app_client.get(f"/api/incidents/{a.id}/evidence")).json()
    assert ev["items"] == [] and ev["total"] == 0
    assert (await app_client.get(f"/api/incidents/{a.id}/hypotheses")).json() == []
    iv = (await app_client.get(f"/api/incidents/{a.id}/interventions")).json()
    assert iv["items"] == []
    pr = (await app_client.get(f"/api/incidents/{a.id}/prompts")).json()
    assert pr["items"] == []


# ---- illegal transitions -> 409 ----------------------------------------------------------------------


async def test_dismiss_then_resolve_is_409(app_client, seeded):
    a, _, _ = seeded
    r = await app_client.post(f"/api/incidents/{a.id}/dismiss", json={"reason": "noise"})
    assert r.status_code == 200 and r.json()["to_state"] == "dismissed"
    again = await app_client.post(f"/api/incidents/{a.id}/dismiss")
    assert again.status_code == 409
    assert again.json()["error"]["type"] == "illegal_transition"
    resolve = await app_client.post(f"/api/incidents/{a.id}/resolve")
    assert resolve.status_code == 409


async def test_resolve_before_verification_is_409(app_client, seeded):
    """An incident that was never measured/verified cannot be closed (detected -> closed is illegal)."""
    a, _, _ = seeded
    r = await app_client.post(f"/api/incidents/{a.id}/resolve")
    assert r.status_code == 409
    d = (await app_client.get(f"/api/incidents/{a.id}")).json()
    assert d["state"] == IncidentState.DETECTED.value


async def test_transition_writes_audit_event(app_client, seeded, session):
    from app.models.core import AuditEvent
    from sqlalchemy import select

    a, _, _ = seeded
    await app_client.post(f"/api/incidents/{a.id}/dismiss", headers={"X-Actor": "alice"})
    rows = (await session.execute(select(AuditEvent).where(AuditEvent.entity_id == str(a.id)))).scalars().all()
    human = [r for r in rows if r.actor == "alice"]
    assert human and human[0].actor_type == "human", "the operator's transition is attributed to the operator"


async def test_unknown_route_has_typed_error(app_client):
    r = await app_client.get("/api/nope")
    assert r.status_code == 404 and r.json()["error"]["type"] == "not_found"


async def test_no_demo_route(app_client):
    assert (await app_client.get("/demo")).status_code == 404
    assert (await app_client.get("/api/demo")).status_code == 404
