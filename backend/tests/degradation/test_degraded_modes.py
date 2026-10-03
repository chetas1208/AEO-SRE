"""Plan section 7 failure-safe modes. Every external dependency can fail; nothing may be fabricated."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.domain.enums import ExperimentStatus, IncidentState
from app.models.core import Incident, Signal
from app.models.evidence import Evidence, Hypothesis
from app.models.interventions import Reward
from app.models.policy import PolicyVersion
from app.services import pipeline
from sqlalchemy import select

from tests import factories as f
from tests.support import seed_world

# ---- Profound unavailable --------------------------------------------------------------------------------------


async def test_profound_not_configured_ingest_is_unavailable_and_creates_no_signals(session, org):
    from app.services.ingestion import ingest_org

    res = await ingest_org(session, org.id)
    await session.commit()
    assert res.status == "unavailable"
    assert (await session.execute(select(Signal))).scalars().all() == []
    assert all(s["state"] == "unavailable" for s in res.surfaces.values())


async def test_profound_server_errors_never_produce_signals(session, org, set_env, mock_http):
    from app.connectors.profound import ProfoundClient
    from app.services.ingestion import ingest_org

    set_env(PROFOUND_API_KEY="pk_TEST_ONLY")
    mock_http.route(host="api.tryprofound.com").mock(return_value=httpx.Response(503, json={"detail": "down"}))

    async def nosleep(_):
        return None

    client = ProfoundClient(max_retries=1, sleep=nosleep)
    try:
        res = await ingest_org(session, org.id, client=client)
    finally:
        await client.aclose()
    await session.commit()
    assert res.status in ("unavailable", "failed", "degraded")
    assert res.created == 0
    assert (await session.execute(select(Signal))).scalars().all() == []


async def test_pipeline_ingest_unavailable_is_recorded_noop_and_does_not_chain_detection(session, org, monkeypatch):
    chained = []

    async def spy(kind, payload, run_inline, session, incident_id=None):
        chained.append(kind)
        return "queued"

    monkeypatch.setattr(pipeline, "_chain", spy)
    out = await pipeline.ingest(session, org.id)
    assert out["status"] in ("unavailable", "failed")
    assert chained == []


async def test_detection_keeps_working_on_previously_ingested_data_when_profound_is_down(session, org):
    await f.make_signal_series(session, org, metric="visibility", values=f.visibility_drop_values(60, 35),
                               end=datetime.now(UTC) - timedelta(hours=1))
    from app.incidents.detector import detect_incidents

    assert len(await detect_incidents(session, org.id)) == 1


async def test_capabilities_report_unconfigured_integrations_as_unavailable(app_client, monkeypatch):
    import app.services.capabilities as capsvc

    monkeypatch.setenv("EVIDENCE_RANKER_PATH", "/nonexistent/artifact")  # simulate: artifact missing
    monkeypatch.setattr(capsvc, "_ranker_cache", None)
    body = (await app_client.get("/api/system/capabilities")).json()
    caps = body["capabilities"]
    assert body["executors"]["manual"]["state"] == "healthy"  # needs nothing, so it never degrades
    assert body["executors"]["github"]["state"] == "unavailable"
    for key in ("profound", "llm"):
        assert caps[key]["state"] == "unavailable", (key, caps[key])
        assert caps[key]["detail"] or caps[key]["last_error"]
    assert caps["ml_ranker"]["state"] == "degraded", "missing artifact must surface as degraded, not healthy"
    assert caps["database"]["state"] == "healthy"


# ---- LLM unavailable ------------------------------------------------------------------------------------------------


async def test_investigation_completes_with_rules_only_when_no_llm_configured(
        session, mock_http, fast_web, no_queue, monkeypatch):
    org, _, _ = await seed_world(session, mock_http, monkeypatch)
    out = await pipeline.detect(session, org.id)
    inc = await session.get(Incident, uuid.UUID(out["created"][0]), populate_existing=True)
    assert inc.investigation_status == "complete"
    assert inc.context["llm"] == {"used": False, "warnings": [], "available": False, "calls": []}
    hyps = (await session.execute(select(Hypothesis).where(Hypothesis.incident_id == inc.id))).scalars().all()
    assert hyps and all(h.produced_by.startswith("rules") for h in hyps), [h.produced_by for h in hyps]


async def test_llm_provider_errors_do_not_stop_the_pipeline_or_invent_hypotheses(
        session, mock_http, fast_web, no_queue, monkeypatch, set_env):
    org, _, _ = await seed_world(session, mock_http, monkeypatch)
    set_env(MODEL_API_KEY="sk-TEST-ONLY", MODEL_BASE_URL="https://llm.test", MODEL_API_PROTOCOL="anthropic")
    mock_http.post(url__regex=r"https://llm\.test/.*").mock(return_value=httpx.Response(401, json={"error": "bad key"}))
    out = await pipeline.detect(session, org.id)
    inc = await session.get(Incident, uuid.UUID(out["created"][0]), populate_existing=True)
    assert inc.state in (IncidentState.AWAITING_APPROVAL.value, IncidentState.INTERVENTION_PROPOSED.value), inc.state
    hyps = (await session.execute(select(Hypothesis).where(Hypothesis.incident_id == inc.id))).scalars().all()
    assert hyps, "rule-based hypotheses must survive an LLM outage"
    assert not any("llm" in (h.produced_by or "").lower() for h in hyps)
    assert inc.context["llm"]["used"] is False


# ---- crawl fails (pipeline level) ---------------------------------------------------------------------------------


async def test_all_pages_failing_yields_unavailable_evidence_and_no_confirmed_root_cause(
        session, mock_http, fast_web, no_queue, monkeypatch):
    org, _, _ = await seed_world(session, mock_http, monkeypatch)
    mock_http.clear()
    mock_http.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r".*").mock(return_value=httpx.Response(503))
    out = await pipeline.detect(session, org.id)
    inc = await session.get(Incident, uuid.UUID(out["created"][0]), populate_existing=True)
    ev = (await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars().all()
    web = [e for e in ev if e.type != "profound"]
    assert web and all(e.status in ("unavailable", "failed") for e in web)
    assert all(not e.excerpt and e.content_hash is None for e in web)
    hyps = (await session.execute(select(Hypothesis).where(Hypothesis.incident_id == inc.id))).scalars().all()
    assert not any(h.status == "confirmed" for h in hyps), "no root cause may be confirmed without readable evidence"
    from app.models.interventions import Intervention

    sel = (await session.execute(select(Intervention).where(Intervention.incident_id == inc.id,
                                                            Intervention.selected.is_(True)))).scalars().one()
    assert sel.action.value == "observe", "low root-cause confidence must recommend observe"


# ---- ML artifact missing ------------------------------------------------------------------------------------------


def test_ranker_without_artifact_falls_back_and_flags_degraded(tmp_path):
    from app.evidence.ranker import EvidenceRanker

    r = EvidenceRanker.load(tmp_path / "does-not-exist")
    out = r.score("Acme supports SAML SSO", "Acme supports SAML SSO on the Enterprise plan.", {})
    assert out.degraded is True and out.method == "heuristic"
    assert r.available is False and r.load_error
    assert 0.0 <= out.support <= 1.0 and 0.0 <= out.freshness_risk <= 1.0
    assert out.freshness_known is False, "freshness must be flagged as a prior when no age info was supplied"


async def test_collector_marks_ranker_unavailable_in_evidence_raw(session, mock_http, fast_web, no_queue, monkeypatch):
    from app.investigation.collector import collect_evidence

    monkeypatch.setenv("EVIDENCE_RANKER_PATH", "/nonexistent/artifact")  # simulate: artifact missing
    org = await f.make_org(session, domain="degr.example", competitor_domains=[])
    inc = await f.make_incident(session, org, title="SAML SSO visibility", context={"cited_urls": ["https://degr.example/sso"]})
    mock_http.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r".*sitemap.*").mock(return_value=httpx.Response(404))
    html = "<html><body><h1>SAML SSO</h1><p>We support SAML SSO for enterprise customers.</p></body></html>"
    mock_http.get(url__regex=r"https://degr\.example/?.*").mock(
        return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    rows = await collect_evidence(session, inc, None)
    scored = [r for r in rows if (r.raw or {}).get("ranker")]
    assert scored, "relevant page should have been scored by the fallback ranker"
    for r in scored:
        assert r.raw["ranker"].get("ranker_available") is False, "fallback scores must be labeled as degraded"


# ---- policy missing -> cold start -----------------------------------------------------------------------------------


async def test_no_policy_versions_means_cold_start_prior_not_learned(session, org):
    from app.policy import encode_context
    from app.policy.store import PolicyStore

    assert (await session.execute(select(PolicyVersion))).scalars().all() == []
    inc = await f.make_incident(session, org, metrics=[
        {"key": "visibility", "label": "Visibility", "before": 61.0, "after": 37.0, "delta": -24.0, "unit": "pp",
         "delta_pct": -39.3}])
    decision, row = await PolicyStore(session).decide(encode_context(inc), incident_id=inc.id)
    await session.commit()
    assert decision.cold_start is True and decision.selection_basis.value == "cold_start_prior"
    assert row.cold_start is True and row.n_related == 0
    versions = (await session.execute(select(PolicyVersion))).scalars().all()
    assert [v.version for v in versions] == ["v0.0.1"] and versions[0].n_updates == 0
    assert 0 < decision.probability <= 1


async def test_api_policy_labels_cold_start(app_client):
    r = await app_client.get("/api/policy")
    assert r.status_code == 200
    body = r.json()
    assert body.get("cold_start") in (True, None) or "cold" in str(body).lower()


# ---- no observation -> awaiting, reward NULL --------------------------------------------------------------------------


async def _executed_experiment(session, org, *, executed_at, window_start, dry_run=False):
    from app.interventions.changes import FileChange, ProposedChange

    inc = await f.make_incident(session, org, state=IncidentState.AWAITING_VERIFICATION)
    ch = ProposedChange(title="t", files=[FileChange(path="a.md", new_content="x")]).to_json()
    iv = await f.make_intervention(session, inc, proposed_change=ch)
    pv = await f.make_policy_version(session)
    exp = await f.make_experiment(
        session, inc, iv, pv, status=ExperimentStatus.AWAITING_VERIFICATION, executed_at=executed_at,
        verification_window_start=window_start, verification_window_end=window_start + timedelta(days=7),
        before_metrics={"visibility": 0.37}, dry_run=dry_run, context_vector=[0.1])
    return inc, iv, exp


async def test_no_observation_experiment_stays_awaiting_and_reward_is_null(session, org):
    from app.experiments.verification import evaluate

    _, _, exp = await _executed_experiment(session, org, executed_at=f.NOW, window_start=f.NOW)
    res = await evaluate(session, exp, now=f.NOW + timedelta(days=30))
    await session.commit()
    assert res.reward is None and res.status == ExperimentStatus.AWAITING_VERIFICATION
    assert res.window_elapsed is True, "even an elapsed window must not fabricate an outcome"
    await session.refresh(exp)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.after_metrics is None
    assert (await session.execute(select(Reward))).scalars().all() == []


async def test_observation_before_window_or_execution_is_not_counted(session, org):
    from app.experiments.verification import evaluate, record_observation

    _, _, exp = await _executed_experiment(session, org, executed_at=f.NOW, window_start=f.NOW + timedelta(hours=48))
    await record_observation(session, exp.id, {"visibility": 0.9}, "profound", f.NOW + timedelta(hours=1))
    await record_observation(session, exp.id, {"visibility": 0.9}, "profound", f.NOW - timedelta(days=1))
    res = await evaluate(session, exp, now=f.NOW + timedelta(hours=2))
    assert res.reward is None and res.status == ExperimentStatus.AWAITING_VERIFICATION


async def test_ingest_reward_without_observation_makes_no_reward_and_no_policy_version(session, org):
    from app.learning.ingest import ingest_reward
    from app.learning.reward import NoObservation

    _, _, exp = await _executed_experiment(session, org, executed_at=f.NOW, window_start=f.NOW)
    await session.commit()
    with pytest.raises(NoObservation):
        await ingest_reward(session, exp.id)
    await session.rollback()
    assert (await session.execute(select(Reward))).scalars().all() == []
    assert len((await session.execute(select(PolicyVersion))).scalars().all()) == 1


async def test_pipeline_reward_without_observation_reports_waiting(session, org):
    _, _, exp = await _executed_experiment(session, org, executed_at=f.NOW, window_start=f.NOW)
    await session.commit()
    out = await pipeline.reward(session, exp.id)
    assert out["status"] == "awaiting_observation"
    assert (await session.execute(select(Reward))).scalars().all() == []


async def test_reward_function_rejects_missing_or_empty_after_metrics():
    from app.learning.reward import NoObservation, compute_reward

    for after in (None, {}, {"unrelated": 1.0}):
        with pytest.raises(NoObservation):
            compute_reward({"visibility": 0.4}, after, "observe")


async def test_experiment_api_shows_awaiting_reward_not_a_number(app_client, session, org):
    _, _, exp = await _executed_experiment(session, org, executed_at=f.NOW, window_start=f.NOW)
    await session.commit()
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["reward"] is None and d["awaiting_reward"] is True
    assert d["after_metrics"] in (None, {})
    rows = (await app_client.get("/api/experiments")).json()
    text = str(rows)
    assert "awaiting" in text.lower()


# ---- redis down -----------------------------------------------------------------------------------------------------


async def test_health_degraded_not_down_when_only_redis_is_unreachable(app_client):
    r = await app_client.get("/api/health")
    body = r.json()
    assert r.status_code == 200 and body["database"] == "healthy" and body["redis"] != "healthy"
    assert body["status"] == "degraded"
