"""FULL LOOP: the 20-step first complete loop, using the real modules end to end.

Only EXTERNAL systems are replaced: public web pages (respx), and the EvidenceRanker
artifact (not trained yet in this repo) by a deterministic TEST-ONLY scorer. Profound signals are RECORDED/TEST ONLY
rows. All detection, investigation, graph, gate, policy, approval, executor, ledger, reward and policy-versioning
code is the real implementation. Nothing here is ever shown as live data.

Steps
 1 fixture signals           2 detect incident          3 investigation starts      4 evidence (incl. unavailable)
 5 evidence graph            6 hypotheses (proposed)    7 evidence gate -> confirmed 8 candidate interventions
 9 policy decision (v0.0.1, cold start)                10 selection + propensity   11 experiment opened (before-state)
12 approval pending          13 human approves (API) -> manual intervention package, experiment activated
14 human records the execution (API)                   15 experiment awaiting_verification, reward NULL
16 verify without observation stays waiting            17 record measured observation
18 reward computed (components)                        19 NEW policy version      20 experiment provenance + audit + SSE log
"""
from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from app.domain.enums import ActionType, ExperimentStatus, IncidentState
from app.models.core import AuditEvent, Incident, IncidentEvent, Signal
from app.models.evidence import Evidence, EvidenceEdge, Hypothesis
from app.models.interventions import Approval, Execution, Experiment, Intervention, Observation, Reward
from app.models.policy import PolicyDecision, PolicyVersion
from app.services import pipeline
from sqlalchemy import select

from tests import factories as f
from tests.helpers import mutating_calls
from tests.support import seed_world


def _utc(dt):
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


@pytest.fixture
async def world(session, mock_http, fast_web, no_queue, monkeypatch):
    return await seed_world(session, mock_http, monkeypatch)


async def to_awaiting_approval(session, org):
    """Steps 2-12 through the real pipeline (detect chains investigation and policy proposal inline)."""
    out = await pipeline.detect(session, org.id)
    assert len(out["created"]) == 1, out
    return uuid.UUID(out["created"][0])


async def refresh(session, model, ident):
    return await session.get(model, ident, populate_existing=True)


async def test_full_loop_20_steps(session, world, mock_http, app_client, set_env, inline_queue):
    org, cluster, now = world
    set_env(VERIFICATION_DELAY_HOURS="0", POLICY_SEED="1")  # no GITHUB_* anywhere: manual executor only

    # 1 fixture signal series exists and is labeled as recorded test data
    sig = (await session.execute(select(Signal).where(Signal.org_id == org.id))).scalars().all()
    assert len(sig) >= 10 and all(s.raw.get("fixture") == "RECORDED/TEST ONLY" for s in sig)

    # 2 -> 12 detect, investigate, gate, policy, propose (inline chain)
    iid = await to_awaiting_approval(session, org)
    inc = await refresh(session, Incident, iid)
    # 2 incident detected from the measured drop
    assert inc.category == "visibility_drop" and "37" in json.dumps(inc.metrics)
    # 3 investigation ran and was logged
    stages = [e.stage for e in (await session.execute(
        select(IncidentEvent).where(IncidentEvent.incident_id == iid).order_by(IncidentEvent.seq))).scalars()]
    assert "investigation.started" in stages and "investigation.completed" in stages
    assert inc.investigation_status == "complete"
    # 4 evidence: profound + web; every item has provenance fields, failed pages carry no content
    ev = (await session.execute(select(Evidence).where(Evidence.incident_id == iid))).scalars().all()
    types = {e.type for e in ev}
    assert {"profound", "competitor"} <= types, types
    for e in ev:
        if e.status in ("unavailable", "failed"):
            assert not e.excerpt and e.content_hash is None
        else:
            assert e.content_hash and e.retrieval_method and e.retrieved_at
    rival = next(e for e in ev if e.type == "competitor" and e.url == "https://rival-loop.example/")
    assert rival.status == "changed", "a new fetch that differs from the stored snapshot must be reported as changed"
    # 5 graph persisted with provenance on every edge
    edges = (await session.execute(select(EvidenceEdge).where(EvidenceEdge.incident_id == iid))).scalars().all()
    assert edges and all({"source", "retrieval_method"} <= set(e.provenance) for e in edges)
    # 6 hypotheses exist
    hyps = (await session.execute(select(Hypothesis).where(Hypothesis.incident_id == iid))).scalars().all()
    assert hyps
    # 7 gate confirmed exactly the hypothesis that passed it; nothing else is confirmed
    confirmed = [h for h in hyps if h.status == "confirmed"]
    assert len(confirmed) >= 1, inc.context.get("gate")
    assert inc.context["gate_primary"]["confirmed"] is True
    audit_states = [r.metadata_.get("to_state") for r in (await session.execute(
        select(AuditEvent).where(AuditEvent.entity_id == str(iid), AuditEvent.event == "state_transition"))).scalars()]
    assert "root_cause_confirmed" in audit_states
    # 8 candidates: several alternatives incl. observe, exactly one selected
    ivs = (await session.execute(select(Intervention).where(Intervention.incident_id == iid))).scalars().all()
    assert len(ivs) >= 2 and ActionType.OBSERVE.value in {i.action.value for i in ivs}
    selected = [i for i in ivs if i.selected]
    assert len(selected) == 1
    iv = selected[0]
    assert iv.action != ActionType.OBSERVE, "confirmed update/missing-content root cause should select a mutating action"
    # 9 policy decision from the cold-start v0.0.1 with stored propensity
    pd = (await session.execute(select(PolicyDecision).where(PolicyDecision.incident_id == iid))).scalars().one()
    pv0 = await session.get(PolicyVersion, pd.policy_version_id)
    assert pv0.version == "v0.0.1" and pv0.n_updates == 0 and pd.cold_start is True
    # 10 selection basis + probability recorded
    assert iv.selection_basis.value in ("cold_start_prior", "rule_fallback") and 0 < pd.probability <= 1
    assert pd.selected_action == iv.action.value
    # 11 experiment opened with measured before-state, no outcome yet
    exp = (await session.execute(select(Experiment).where(Experiment.incident_id == iid))).scalars().one()
    assert exp.status == ExperimentStatus.PROPOSED
    assert exp.before_metrics.get("visibility") in (37, 37.0, 0.37)
    assert exp.after_metrics is None and exp.policy_version_id == pv0.id and exp.policy_probability == pd.probability
    assert exp.alternatives, "alternatives with bandit scores must be recorded"
    assert (await session.execute(select(Reward))).scalars().all() == []
    # 12 approval is pending and incident waits for a human; nothing executed
    ap = (await session.execute(select(Approval).where(Approval.intervention_id == iv.id))).scalars().one()
    assert ap.status.value == "pending" and inc.state == IncidentState.AWAITING_APPROVAL.value
    assert (await session.execute(select(Execution))).scalars().all() == []
    assert mutating_calls(mock_http) == []

    # 13 execute before approval is rejected; then a human approves via the API, which ACTIVATES the experiment
    premature = await app_client.post(f"/api/interventions/{iv.id}/execute")
    assert premature.status_code == 409
    assert mutating_calls(mock_http) == []
    r = await app_client.post(f"/api/interventions/{iv.id}/approve", json={"note": "ship it"},
                              headers={"X-Actor": "alice@loopco.example"})
    assert r.status_code == 200, r.text
    assert r.json()["incident_state"] == IncidentState.APPROVED.value
    out_iv = r.json()["intervention"]
    assert out_iv["manual_execution_pending"] is True and out_iv["executor"] == "manual"
    pkg = out_iv["package"]
    assert pkg["steps"] and pkg["rollback"] and pkg["evidence_summary"] and pkg["target"]["paths"]
    assert pkg["changes"][0]["proposed_text"] and "Mark as executed" in " ".join(pkg["steps"])
    exp = await refresh(session, Experiment, exp.id)
    assert exp.status == ExperimentStatus.APPROVED and exp.executed_at is None
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.status == "awaiting_human_execution" and ex.executor == "manual" and ex.dry_run is False
    assert ex.approval_id == ap.id and ex.package == pkg
    cta = (await app_client.get(f"/api/incidents/{iid}")).json()["primary_cta"]
    assert cta["key"] == "mark_executed" and cta["label"] == "Mark as executed"
    assert mutating_calls(mock_http) == [] and not [c for c in mock_http.calls if "github" in str(c.request.url.host)]

    # 14 the human applies it and records the execution (a non-human actor cannot)
    bad = await app_client.post(f"/api/interventions/{iv.id}/executed", json={}, headers={"X-Actor": "pipeline"})
    assert bad.status_code == 403
    applied_at = datetime.now(UTC) - timedelta(minutes=30)
    r = await app_client.post(f"/api/interventions/{iv.id}/executed", headers={"X-Actor": "alice@loopco.example"},
                              json={"executed_at": applied_at.isoformat(), "reference_url": "https://loopco.example/sso",
                                    "note": "published as proposed"})
    assert r.status_code == 200, r.text
    assert r.json()["deviation"] is False and r.json()["incident_state"] == IncidentState.AWAITING_VERIFICATION.value
    assert (await app_client.post(f"/api/interventions/{iv.id}/executed", json={})).status_code == 409
    iv = await refresh(session, Intervention, iv.id)
    ex = await refresh(session, Execution, ex.id)
    assert ex.dry_run is False and ex.status == "succeeded" and ex.executor == "manual"
    assert ex.reference == "https://loopco.example/sso" and ex.executed_by == "alice@loopco.example"
    # 15 experiment is awaiting verification (window anchored at the human's executed_at) and the reward is NULL
    exp = await refresh(session, Experiment, exp.id)
    inc = await refresh(session, Incident, iid)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.dry_run is False
    assert exp.after_metrics is None and exp.execution_reference == ex.reference and exp.executor == "manual"
    assert abs((_utc(exp.executed_at) - applied_at).total_seconds()) < 1  # SQLite returns tz-naive datetimes
    assert abs((_utc(exp.verification_window_start) - applied_at).total_seconds()) < 1  # VERIFICATION_DELAY_HOURS=0
    assert inc.state == IncidentState.AWAITING_VERIFICATION.value
    assert (await session.execute(select(Reward))).scalars().all() == []
    assert (await app_client.get(f"/api/experiments/{exp.id}")).json()["summary"]["display_status"] == "Awaiting Measurement"

    # 16 verification without any post-intervention observation must not fabricate anything
    out = await pipeline.verify(session, exp.id)
    assert out["status"] in ("awaiting_observation", "awaiting_window"), out
    exp = await refresh(session, Experiment, exp.id)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION and exp.after_metrics is None
    assert (await session.execute(select(Reward))).scalars().all() == []
    assert (await session.execute(select(Observation))).scalars().all() == []
    assert (await session.execute(func_count_versions())).scalar() == 1

    # 17 measured post-intervention Profound observation arrives (RECORDED/TEST ONLY), verification records it
    await f.make_signal_series(session, org, metric="visibility", values=[52.0], end=datetime.now(UTC) + timedelta(minutes=5),
                               cluster=cluster)
    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(datetime.now(UTC) + timedelta(minutes=10))):  # the observation is stamped "now + 5 min"
        out = await pipeline.verify(session, exp.id, force=True)
    assert out["status"] in ("verified", "rewarded"), out
    obs = (await session.execute(select(Observation).where(Observation.experiment_id == exp.id))).scalars().all()
    assert len(obs) == 1 and obs[0].source == "profound"
    exp = await refresh(session, Experiment, exp.id)
    assert exp.after_metrics and exp.after_metrics["visibility"] == 52.0

    # 18 reward computed from measured components only
    rw = (await session.execute(select(Reward).where(Reward.experiment_id == exp.id))).scalars().one()
    assert set(rw.components) == {"visibility", "action_cost", "risk_penalty"}, "only measured metrics are persisted"
    assert rw.components["visibility"] > 0 and "citation" not in rw.components, "visibility rose; no citation data measured"
    assert rw.total > 0 and rw.weights
    assert exp.status == ExperimentStatus.REWARDED

    # 19 a NEW immutable policy version was written (parent v0.0.1), the old one untouched
    versions = (await session.execute(select(PolicyVersion).order_by(PolicyVersion.n_updates))).scalars().all()
    assert [v.version for v in versions] == ["v0.0.1", "v0.0.2"]
    v1 = versions[1]
    assert v1.parent_id == versions[0].id and v1.n_updates == 1 and v1.source_experiment_id == exp.id
    assert versions[0].n_updates == 0

    # 20 experiment provenance links everything; audit + SSE trail complete; incident reached rewarded
    assert exp.policy_version_id == versions[0].id, "experiment must record the version that CHOSE the action"
    assert exp.approver == "alice@loopco.example" and exp.approval_id == ap.id
    inc = await refresh(session, Incident, iid)
    assert inc.state == IncidentState.REWARDED.value
    audit_events = {r.event for r in (await session.execute(select(AuditEvent))).scalars()}
    assert {"approval.requested", "approval.approved", "policy_updated"} <= audit_events
    final_stages = {e.stage for e in (await session.execute(
        select(IncidentEvent).where(IncidentEvent.incident_id == iid))).scalars()}
    assert {"policy.completed", "approval.pending", "execution.completed", "reward.completed",
            "policy.updated"} <= final_stages
    detail = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert detail["reward"]["total"] == pytest.approx(rw.total) and detail["awaiting_reward"] is False
    base64.b64encode(b"")  # (keeps import used)


def func_count_versions():
    from sqlalchemy import func

    return select(func.count()).select_from(PolicyVersion)


async def test_gate_confirms_natively(session, world):
    """Regression (was P0-1): with a changed competitor page, a measured Profound drop and well-scored evidence, the real pipeline
    must be able to confirm a root cause by itself."""
    org, _, _ = world
    iid = await to_awaiting_approval(session, org)
    inc = await refresh(session, Incident, iid)
    assert (inc.context.get("gate_primary") or {}).get("confirmed") is True, inc.context.get("gate")


async def test_policy_context_reflects_detected_metrics(session, world):
    """Regression (was P0-2): the context vector fed to the bandit must carry the measured deltas of the incident."""
    from app.incidents.detector import detect_incidents
    from app.policy import encode_context
    from app.policy.features import FEATURE_NAMES

    org, _, now = world
    (inc,) = await detect_incidents(session, org.id, now=now)
    x = dict(zip(FEATURE_NAMES, encode_context(inc), strict=False))
    assert x["visibility_delta"] < 0, "visibility fell 61 -> 37 but the policy context says 0"
    assert x["competitor_delta"] > 0, "competitor share rose 21 -> 54 but the policy context says 0"


async def test_loop_optional_github_dry_run_preview_changes_nothing_and_never_rewards(
        session, world, mock_http, app_client, inline_queue, set_env):
    """OPTIONAL/SECONDARY. An operator explicitly selects the GitHub executor as a dry-run preview: zero HTTP, the
    experiment is flagged dry_run, cannot be verified, and no reward / policy version is ever produced from it.
    (The manual executor is NOT a dry-run; see test_full_loop_20_steps.)"""
    org, cluster, _ = world
    set_env(POLICY_SEED="1")  # the bandit samples actions; pin it so this branch always gets a mutating action
    iid = await to_awaiting_approval(session, org)
    iv = (await session.execute(select(Intervention).where(Intervention.incident_id == iid,
                                                           Intervention.selected.is_(True)))).scalars().one()
    iv_id = iv.id
    assert (await app_client.post(f"/api/interventions/{iv_id}/approve", headers={"X-Actor": "alice"})).status_code == 200
    r = await app_client.post(f"/api/interventions/{iv_id}/execute", json={"executor": "github", "dry_run": True})
    assert r.status_code == 202, r.text
    assert mutating_calls(mock_http) == []
    assert not [c for c in mock_http.calls if "github" in str(c.request.url.host)], "dry-run must not touch GitHub at all"
    ex = (await session.execute(select(Execution).where(Execution.executor == "github_pr"))).scalars().one()
    assert ex.dry_run is True and ex.reference is None
    exp = (await session.execute(select(Experiment).where(Experiment.incident_id == iid))).scalars().one()
    await session.refresh(exp)
    assert exp.dry_run is True and exp.status == ExperimentStatus.EXECUTED
    assert exp.after_metrics is None and exp.verification_window_start is None
    v = await app_client.post(f"/api/experiments/{exp.id}/verify", params={"force": True})
    assert v.status_code == 409, "a dry-run experiment must not be verifiable, even when forced"
    out = await pipeline.verify(session, exp.id, force=True)
    assert out["status"] == "not_verifiable"
    assert (await session.execute(select(Reward))).scalars().all() == []
    versions = (await session.execute(select(PolicyVersion))).scalars().all()
    assert [v.version for v in versions] == ["v0.0.1"], "no policy update may come from a dry run"


async def test_real_ranker_is_called_by_pipeline_and_scores_land_on_evidence(session, mock_http, fast_web, no_queue,
                                                                              monkeypatch):
    """V2: with the trained EvidenceRanker (not the fixture stub) the investigation writes model scores + provenance
    onto Evidence rows. The gate outcome is NOT asserted: see docs/notes/audit-v2.md (claim/ranker mismatch)."""
    from app.evidence.ranker import EvidenceRanker

    mock_http.route(host__regex=r".*huggingface\.co").pass_through()  # cached MiniLM weights; no mocked-network error
    ranker = EvidenceRanker.load()
    if not ranker.available or ranker.degraded:
        pytest.skip(f"trained ranker unavailable here: {ranker.status()}")
    import app.investigation.collector as col

    org, _, _ = await seed_world(session, mock_http, monkeypatch)
    monkeypatch.setattr(col, "_load_ranker", lambda: ranker)  # after seed_world's stub
    iid = await to_awaiting_approval(session, org)
    web = [e for e in (await session.execute(select(Evidence).where(Evidence.incident_id == iid))).scalars()
           if e.type in ("owned", "competitor") and e.status in ("live", "changed")]
    assert web
    for e in web:
        assert e.support_score is not None and 0.0 <= e.support_score <= 1.0
        assert e.raw["ranker"]["method"].startswith("model:") and e.raw["ranker"]["degraded"] is False
        assert e.raw["ranker"]["version"] == ranker.version
