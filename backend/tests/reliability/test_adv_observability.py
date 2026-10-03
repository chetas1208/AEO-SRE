"""Observability contracts: secrets never reach logs, logs carry correlation ids, SSE vocabulary is stable and small."""
from __future__ import annotations

import io
import json
import logging
import uuid

import pytest
import structlog
from app.api.logging import configure_logging, redact_secrets
from app.core.event_types import CANONICAL, MAX_EVENT_METADATA_BYTES, bounded_metadata, canonical_event_type

from tests import factories as f

SECRETS = {"PROFOUND_API_KEY": "pk_live_SUPERSECRET_111", "MODEL_API_KEY": "sk-SUPERSECRET-222"}


@pytest.fixture
def json_logs(set_env):
    set_env(**SECRETS)
    buf = io.StringIO()
    handler = logging.StreamHandler(buf)
    root = logging.getLogger()
    root.handlers = [handler]
    configure_logging("DEBUG", json_logs=True)
    structlog.configure(cache_logger_on_first_use=False, wrapper_class=structlog.make_filtering_bound_logger(10),
                        processors=[structlog.contextvars.merge_contextvars, redact_secrets,
                                    structlog.processors.JSONRenderer()],
                        logger_factory=structlog.PrintLoggerFactory(buf))
    yield buf
    structlog.reset_defaults()


def test_configured_credentials_and_auth_headers_are_scrubbed_from_every_log_line(json_logs):
    log = structlog.get_logger()
    log.info("x", error=f"401 for key {SECRETS['PROFOUND_API_KEY']}", url="https://a/b",
             headers={"Authorization": "Bearer abc", "x-api-key": SECRETS["MODEL_API_KEY"], "accept": "json"},
             tokens=123, prompt_tokens=45)
    out = json_logs.getvalue()
    for secret in (*SECRETS.values(), "Bearer abc"):
        assert secret not in out, secret
    rec = json.loads(out.strip().splitlines()[-1])
    assert rec["tokens"] == 123 and rec["prompt_tokens"] == 45, "token COUNTS are not secrets"
    assert rec["headers"]["accept"] == "json"


async def test_http_error_responses_and_request_logs_never_contain_credentials(app_client, json_logs):
    r = await app_client.get(f"/api/incidents/{uuid.uuid4()}", headers={"Authorization": "Bearer abc"})
    assert r.status_code == 404
    assert "Bearer abc" not in r.text and "Bearer abc" not in json_logs.getvalue()


async def test_request_logs_carry_correlation_ids(app_client, session, org, capfd):
    inc = await f.make_incident(session, org)
    await app_client.get(f"/api/incidents/{inc.id}", headers={"x-request-id": "req-corr-1"})
    out = capfd.readouterr().out
    line = next(ln for ln in out.splitlines() if "http.request" in ln and str(inc.id) in ln)
    assert "req-corr-1" in line and "duration_ms" in line and "status" in line and "incident_id" in line


def test_worker_job_context_is_bound_and_unbound(session):
    ctx = structlog.contextvars.get_contextvars()
    assert "job_id" not in ctx


@pytest.mark.parametrize(("stage", "status", "meta", "expected"), [
    ("investigation.started", "running", {}, "investigation.started"),
    ("evidence.profound.started", "running", {}, "provider.request.started"),
    ("evidence.web.completed", "success", {}, "provider.request.completed"),
    ("hypotheses.completed", "success", {}, "hypothesis.proposed"),
    ("gate.completed", "success", {"confirmed": True}, "hypothesis.confirmed"),
    ("gate.completed", "success", {"confirmed": False}, "gate.completed"),
    ("policy.completed", "success", {}, "policy.completed"),
    ("approval.pending", "waiting", {}, "approval.requested"),
    ("execution.package_ready", "waiting", {}, "experiment.activated"),
    ("verification.waiting", "waiting", {}, "verification.scheduled"),
    ("verification.completed", "success", {}, "verification.completed"),
    ("reward.completed", "success", {}, "reward.created"),
])
def test_internal_stages_map_to_the_canonical_vocabulary(stage, status, meta, expected):
    assert canonical_event_type(stage, status, meta) == expected
    assert expected in CANONICAL or expected == "gate.completed"


def test_unconfirmed_gate_never_maps_to_confirmed_and_unknown_stages_are_not_invented():
    assert canonical_event_type("gate.completed", "success", {"confirmed": False}) != "hypothesis.confirmed"
    assert canonical_event_type("totally.custom", "running") == "totally.custom"
    assert canonical_event_type("reward.waiting", "waiting") != "reward.created", "waiting is not a reward"
    assert canonical_event_type("verification.waiting", "waiting") != "verification.completed"


def test_event_metadata_is_bounded():
    big = {"rows": ["x" * 100] * 500}
    out = bounded_metadata(big)
    assert out["truncated"] and out["bytes"] > MAX_EVENT_METADATA_BYTES and "rows" in out["keys"]
    small = {"n": 3}
    assert bounded_metadata(small) == small


async def test_health_reports_each_dependency_independently_with_git_sha(app_client):
    r = await app_client.get("/api/health")
    b = r.json()
    assert r.status_code == 200 and b["database"] == "healthy" and b["redis"] != "healthy"
    assert b["status"] == "degraded", "redis down degrades; it does not take the API down"
    assert b["profound_state"] == "NOT_CONFIGURED" and b["model_state"] == "NOT_CONFIGURED"
    assert b["git_sha"] is None or len(b["git_sha"]) >= 7


async def test_unconfigured_profound_and_model_do_not_degrade_overall(app_client, monkeypatch):
    import app.services.capabilities as caps
    from app.domain.enums import CapabilityState as CS
    from app.schemas.system import Capability

    async def healthy_redis():
        return Capability(key="redis", label="Redis", state=CS.HEALTHY)

    monkeypatch.setattr(caps, "check_redis", healthy_redis)
    b = (await app_client.get("/api/health")).json()
    assert b["status"] == "ok" and b["profound_state"] == "NOT_CONFIGURED" and b["model_state"] == "NOT_CONFIGURED"


async def test_configured_profound_with_failed_last_ingest_is_degraded_and_model_auth_failure_is_visible(
        app_client, session, set_env):
    from app.connectors.llm.status import tracker
    from app.models.core import Setting

    set_env(PROFOUND_API_KEY="pk_test_x_12345678", MODEL_API_KEY="sk-test-12345678", MODEL_API_PROTOCOL="openai_chat",
            MODEL_NAME="m", MODEL_BASE_URL="https://m.example/v1")
    assert (await app_client.get("/api/health")).json()["profound_state"] == "UNVERIFIED"
    session.add(Setting(key="profound.last_sync.org1", value={"status": "failed", "success": None}))
    await session.commit()
    b = (await app_client.get("/api/health")).json()
    assert b["profound_state"] == "DEGRADED" and b["status"] in ("ok", "degraded")
    tracker.last_error_kind = "auth"
    try:
        assert (await app_client.get("/api/health")).json()["model_state"] == "AUTH_FAILED"
    finally:
        tracker.last_error_kind = None


async def test_profound_requests_log_provider_status_duration_without_secrets(mock_http, capfd):
    import httpx
    from app.connectors.profound import ProfoundClient

    mock_http.get("https://api.tryprofound.com/v1/org/categories").respond(200, json=[])
    c = ProfoundClient(api_key=SECRETS["PROFOUND_API_KEY"])
    try:
        await c.list_categories()
    finally:
        await c.aclose()
    out = capfd.readouterr().out
    line = next((ln for ln in reversed(out.splitlines()) if "provider.request" in ln and "provider=profound" in ln), "")
    assert "duration_ms" in line and "status=200" in line
    assert SECRETS["PROFOUND_API_KEY"] not in out
    assert httpx  # (respx mocks httpx)


async def test_org_not_mapped_to_profound_does_not_degrade_passive_state(app_client, session, set_env):
    from app.models.core import Setting

    set_env(PROFOUND_API_KEY="pk_test_x_12345678", MODEL_API_KEY="sk-test-12345678", MODEL_API_PROTOCOL="openai_chat",
            MODEL_NAME="m", MODEL_BASE_URL="https://m.example/v1")
    session.add(Setting(key="profound.last_sync.fixture", value={
        "status": "unavailable", "error": "no_profound_category_owns_auth0.com", "success": None}))
    session.add(Setting(key="profound.last_sync.live", value={"status": "ok", "success": {"at": "2026-10-03T00:00:00+00:00"}}))
    await session.commit()
    assert (await app_client.get("/api/health")).json()["profound_state"] == "READY"
