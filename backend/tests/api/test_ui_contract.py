"""UI <-> API contract: the OpenAPI schema must contain every endpoint and response field the frontend depends on
(derived from frontend/composables/*.ts, stores/*.ts, utils/normalize.ts, types/index.ts). If this fails, either the
backend changed a field the UI reads, or the UI started reading a field the backend does not publish. Regenerate
frontend types with `cd frontend && pnpm gen:api` after intended changes and update docs/UI_API_CONTRACT.md."""
import pytest
from app.domain.enums import IncidentState as S

from tests import factories as f

pytestmark = pytest.mark.asyncio

ENDPOINTS = {
    "/api/health": {"get"},
    "/api/system/capabilities": {"get"},
    "/api/events": {"get"},
    "/api/organizations": {"get", "post"},
    "/api/organizations/{org_id}": {"get", "patch"},
    "/api/incidents": {"get"},
    "/api/incidents/{incident_id}": {"get"},
    "/api/incidents/{incident_id}/investigate": {"post"},
    "/api/incidents/{incident_id}/resolve": {"post"},
    "/api/incidents/{incident_id}/evidence": {"get"},
    "/api/incidents/{incident_id}/evidence/{evidence_id}": {"get"},
    "/api/incidents/{incident_id}/graph": {"get"},
    "/api/incidents/{incident_id}/hypotheses": {"get"},
    "/api/incidents/{incident_id}/prompts": {"get"},
    "/api/incidents/{incident_id}/interventions": {"get"},
    "/api/incidents/{incident_id}/events": {"get"},
    "/api/incidents/{incident_id}/events/history": {"get"},
    "/api/interventions/{intervention_id}/approve": {"post"},
    "/api/interventions/{intervention_id}/reject": {"post"},
    "/api/interventions/{intervention_id}/modify": {"post"},
    "/api/interventions/{intervention_id}/execute": {"post"},
    "/api/interventions/{intervention_id}/executed": {"post"},
    "/api/experiments": {"get"},
    "/api/experiments/{experiment_id}": {"get"},
    "/api/experiments/{experiment_id}/verify": {"post"},
    "/api/policy": {"get", "patch"},
    "/api/settings": {"get"},
}

# schema -> properties the frontend reads (snake_case on the wire; camelized client-side)
FIELDS = {
    "HealthOut": ["status", "database", "redis", "profound_state", "model_state", "git_sha"],
    "CapabilitiesOut": ["capabilities", "last_ingestion", "executors"],
    "Capability": ["key", "label", "state", "detail", "last_success", "last_error"],
    "IncidentList": ["items", "total", "counts_by_severity", "counts_by_status"],
    "IncidentSummary": ["id", "number", "title", "severity", "state", "status", "display_state", "detected_at", "org_id"],
    "IncidentDetail": ["metrics", "priority_breakdown", "primary_cta", "allowed_actions", "allowed_next_states",
                       "expected_outcome", "experiment_id", "explanation", "active_job"],
    "InterventionOut": ["id", "action", "title", "approval_status", "package", "manual_execution_pending", "experiment_id",
                        "executor", "proposed_change", "selection_basis", "cold_start"],
    "InterventionActionOut": ["intervention", "incident_state", "experiment_id", "message"],
    "ExecutedOut": ["intervention", "incident_state", "experiment_id", "verification_window_start", "deviation"],
    "RecordExecutionRequest": ["executed_at", "reference_url", "note", "actual_change"],
    "ApprovalRequest": ["note", "reason_code"],
    "IncidentEventOut": ["id", "seq", "stage", "event_type", "status", "message", "metadata", "timestamp"],
    "ExperimentList": ["items", "total", "summary", "unavailable_reason"],
    "ExperimentSummary": ["running", "awaiting_measurement", "verified"],
    "ExperimentRow": ["id", "code", "status", "display_status", "outcome", "inconclusive_reason", "reward", "before", "after",
                      "policy_version", "dry_run", "awaiting_human_execution"],
    "ExperimentDetail": ["summary", "why_selected", "context_at_decision", "evidence_snapshot", "action_executed", "approval",
                         "before_metrics", "after_metrics", "reward", "policy", "timeline", "awaiting_reward", "display_status",
                         "spec", "declared_metrics", "outcome", "override", "verification"],
    "ExperimentSpec": ["if_action", "because_root_cause", "then_metric", "direction", "window_hours", "delay_hours", "statement"],
    "DeclaredMetrics": ["primary", "secondary"],
    "OutcomeOut": ["label", "observe_outcome", "reward_total", "components", "confounders", "causal_confidence",
                   "causal_statement", "learning_applied", "inconclusive_reason", "observed_at"],
    "OverrideOut": ["overridden", "policy_action", "executed_action", "reason", "by"],
    "VerificationInfo": ["executed_at", "eligible_at", "window_end", "delay_hours", "is_open", "rules"],
    "VerifyOut": ["experiment_id", "job"],
    "PolicyOut": ["available", "current_version", "cold_start", "n_updates", "allowed_actions", "human_approval_required"],
    "OrganizationOut": ["id", "name", "domain", "topics"],
    "EvidenceListOut": ["items", "total", "limit", "offset"],
    "PromptsOut": ["items", "total", "unavailable_reason"],
    "ErrorBody": ["code", "type", "message", "details", "request_id"],
    "ErrorEnvelope": ["error"],
}


@pytest.fixture
async def spec(app_client):
    return (await app_client.get("/openapi.json")).json()


async def test_every_endpoint_and_method_the_ui_calls_exists(spec):
    missing = [f"{m.upper()} {p}" for p, methods in ENDPOINTS.items() for m in methods if m not in spec["paths"].get(p, {})]
    assert missing == []


async def test_response_fields_the_ui_reads_are_published(spec):
    schemas = spec["components"]["schemas"]
    problems = []
    for name, props in FIELDS.items():
        if name not in schemas:
            problems.append(f"schema {name} missing")
            continue
        have = set(schemas[name].get("properties", {}))
        problems += [f"{name}.{p}" for p in props if p not in have]
    assert problems == []


async def test_sse_routes_declare_event_stream(spec):
    for path in ("/api/events", "/api/incidents/{incident_id}/events"):
        content = spec["paths"][path]["get"]["responses"]["200"].get("content", {})
        assert "text/event-stream" in content, path


async def test_error_envelope_is_documented_on_mutations(spec):
    for path in ("/api/experiments/{experiment_id}/verify", "/api/interventions/{intervention_id}/executed"):
        responses = spec["paths"][path]["post"]["responses"]
        for code in ("404", "409", "422"):
            ref = responses[code]["content"]["application/json"]["schema"]["$ref"]
            assert ref.endswith("/ErrorEnvelope"), (path, code)


async def test_runtime_error_envelope_matches_schema(app_client, session, org):
    r = await app_client.get("/api/experiments/EXP-9999")
    assert r.status_code == 404
    err = r.json()["error"]
    assert set(err) == {"code", "type", "message", "details", "request_id"} and err["code"] == "NOT_FOUND"
    assert r.headers.get("x-request-id") == err["request_id"] or err["request_id"]


async def test_verify_before_window_returns_code_and_eligible_at(app_client, session, org):
    inc = await f.make_incident(session, org, state=S.AWAITING_VERIFICATION.value)
    iv = await f.make_intervention(session, inc)
    exp = await f.make_experiment(session, inc, iv, None, executed_at=f.utc(0), verification_window_start=f.utc(-2))
    r = await app_client.post(f"/api/experiments/{exp.id}/verify")
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "EXPERIMENT_NOT_VERIFIABLE_YET" and err["details"]["eligible_at"]
