"""OPTIONAL/SECONDARY: GitHub executor against the real DB with GitHub HTTP mocked (respx). Dry-run => zero mutating
HTTP. Also pins the default: run_execution/pipeline.execute without an explicit choice use the MANUAL executor."""
from __future__ import annotations

import httpx
import pytest
from app.core.config import Settings
from app.domain.enums import ApprovalStatus, ExperimentStatus, IncidentState
from app.experiments import ledger
from app.interventions.changes import FileChange, ProposedChange, wrap_section
from app.interventions.executor import ExecutionRefused, ExecutionStatus, GitHubPRExecutor
from app.interventions.service import run_execution
from app.models.interventions import Approval, Execution, Experiment
from app.services import approvals as appr
from sqlalchemy import select

from tests import factories as f
from tests.helpers import mutating_calls

REPO = "https://api.github.com/repos/acme/site"


def cfg(**kw) -> Settings:
    base = dict(github_token="ghp_TEST_ONLY", github_owner="acme", github_repo="site", github_base_branch="main",
                app_base_url="https://aeo.test")
    return Settings(_env_file=None, **{**base, **kw})


def change(text="SAML SSO is supported on the Enterprise plan.") -> dict:
    return ProposedChange(
        title="Add SAML section", target_url="https://testco.example/enterprise/security", summary="s",
        files=[FileChange(path="content/security.md", old_content="# Security\n",
                          new_content=wrap_section("incident-1", f"## SAML SSO\n\n{text}"),
                          patch_mode="upsert_section", section_id="incident-1")],
    ).to_json()


def mock_github(router, *, fail_refs: bool = False):
    router.get(f"{REPO}/git/ref/heads/main").respond(200, json={"object": {"sha": "basesha"}})
    router.get(f"{REPO}/pulls").respond(200, json=[])
    router.get(url__regex=rf"{REPO}/git/ref/heads/aeo.*").respond(404, json={"message": "Not Found"})
    router.post(f"{REPO}/git/refs").respond(500 if fail_refs else 201, json={})
    router.get(url__regex=rf"{REPO}/contents/.*").respond(404, json={"message": "Not Found"})
    router.put(url__regex=rf"{REPO}/contents/.*").respond(201, json={"commit": {"sha": "c1"}})
    router.post(f"{REPO}/pulls").respond(201, json={"number": 7, "html_url": "https://github.com/acme/site/pull/7"})


@pytest.fixture
async def setup(session, org):
    inc = await f.make_incident(session, org, state=IncidentState.AWAITING_APPROVAL)
    iv = await f.make_intervention(session, inc, proposed_change=change())
    pv = await f.make_policy_version(session)
    exp = await ledger.open_experiment(
        session, iv, {"probability": 0.6, "action": iv.action, "scores": [], "selection_basis": "cold_start_prior",
                      "cold_start": True, "policy_version_id": pv.id},
        inc, {"visibility": 0.37}, {}, [0.1], now=f.NOW)
    await session.commit()
    return inc, iv, exp


async def approve(session, iv, inc, *, status=ApprovalStatus.APPROVED, modified=None, actor="alice"):
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, status, actor, "t", modified)
    inc.state = IncidentState.APPROVED.value
    await session.commit()
    return ap


# ---- the executor service gates on require_executable_approval -----------------------------------------------


async def test_run_execution_refuses_without_approval_and_makes_no_http_calls(session, setup, mock_http):
    _, iv, _ = setup
    with pytest.raises(ExecutionRefused):
        await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    assert len(mock_http.calls) == 0
    assert (await session.execute(select(Execution))).scalars().all() == []


@pytest.mark.parametrize("status", [ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED])
async def test_run_execution_refuses_rejected_or_expired(session, setup, mock_http, status):
    inc, iv, _ = setup
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, status, "alice" if status == ApprovalStatus.REJECTED else "system",
                               actor_type="human" if status == ApprovalStatus.REJECTED else "system")
    await session.commit()
    with pytest.raises(ExecutionRefused):
        await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    assert len(mock_http.calls) == 0


async def test_run_execution_refuses_pending_approval(session, setup, mock_http):
    _, iv, _ = setup
    await appr.request_approval(session, iv)
    await session.commit()
    with pytest.raises(ExecutionRefused):
        await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    assert len(mock_http.calls) == 0


# ---- dry run: zero mutations ------------------------------------------------------------------------------------


async def test_default_executor_is_manual_awaiting_human_and_not_a_dry_run(session, setup, mock_http):
    inc, iv, _ = setup
    await approve(session, iv, inc)
    result, row = await run_execution(session, iv.id, dry_run=None)  # no GitHub credentials, no executor choice
    await session.commit()
    assert result.status == ExecutionStatus.AWAITING_HUMAN_EXECUTION and result.dry_run is False
    assert row.executor == "manual" and row.dry_run is False and row.status == "awaiting_human_execution"
    assert row.package and row.package["changes"] and row.package["steps"]
    assert result.external_mutation is False and not result.api_calls
    assert len(mock_http.calls) == 0, "the manual executor never touches the network"


async def test_explicit_github_choice_without_config_is_refused_not_silently_dry_run(session, setup, mock_http):
    inc, iv, _ = setup
    await approve(session, iv, inc)
    with pytest.raises(ExecutionRefused) as e:
        await run_execution(session, iv.id, choice="github")
    assert e.value.code == "github_not_configured"
    assert (await session.execute(select(Execution))).scalars().all() == []
    assert len(mock_http.calls) == 0


async def test_configured_but_forced_dry_run_issues_no_mutating_http(session, setup, mock_http):
    inc, iv, _ = setup
    await approve(session, iv, inc)
    mock_github(mock_http)
    result, _ = await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=True, settings=cfg()))
    assert result.dry_run is True
    assert mutating_calls(mock_http) == []
    assert not any(c.executed and c.mutating for c in result.api_calls)


async def test_dry_run_experiment_is_flagged_and_never_verifiable(session, setup, mock_http):
    from app.experiments.verification import ExperimentStateError, record_observation

    inc, iv, exp = setup
    await approve(session, iv, inc)
    await ledger.attach_approval(session, exp, (await session.execute(select(Approval))).scalar_one())
    await session.commit()
    await run_execution(session, iv.id, executor=GitHubPRExecutor(settings=cfg()))  # explicit GitHub preview
    await session.commit()
    await session.refresh(exp)
    assert exp.dry_run is True
    assert exp.status == ExperimentStatus.EXECUTED, "a dry run must not advance to awaiting_verification"
    assert exp.verification_window_start is None
    with pytest.raises(ExperimentStateError):
        await record_observation(session, exp.id, {"visibility": 0.9}, "profound", f.NOW)


# ---- live (mocked) execution --------------------------------------------------------------------------------------


async def test_live_execution_opens_pr_and_never_merges(session, setup, mock_http):
    inc, iv, exp = setup
    await approve(session, iv, inc)
    await ledger.attach_approval(session, exp, (await session.execute(select(Approval))).scalar_one())
    await session.commit()
    mock_github(mock_http)
    result, row = await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    await session.commit()
    assert result.status == ExecutionStatus.SUCCEEDED and result.pr_url.endswith("/pull/7")
    methods_paths = {(c.request.method, c.request.url.path) for c in mutating_calls(mock_http)}
    assert ("POST", "/repos/acme/site/git/refs") in methods_paths
    assert ("POST", "/repos/acme/site/pulls") in methods_paths
    assert not any("merge" in p for _, p in methods_paths), "PRs must never be merged automatically"
    assert not any(m == "DELETE" for m, _ in methods_paths)
    assert row.reference == result.pr_url and row.dry_run is False
    await session.refresh(exp)
    assert exp.status == ExperimentStatus.AWAITING_VERIFICATION
    assert exp.after_metrics is None, "no outcome may exist right after execution"
    assert exp.executed_at is not None and exp.execution_reference == result.pr_url


async def test_modified_diff_is_what_gets_committed(session, setup, mock_http):
    import base64
    import json

    inc, iv, exp = setup
    edited = change("EDITED BY HUMAN: SAML 2.0 only.")
    await approve(session, iv, inc, status=ApprovalStatus.MODIFIED, modified=edited)
    await ledger.attach_approval(session, exp, (await session.execute(select(Approval))).scalar_one())
    await session.commit()
    mock_github(mock_http)
    await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    puts = [c for c in mock_http.calls if c.request.method == "PUT"]
    assert puts
    committed = base64.b64decode(json.loads(puts[0].request.content)["content"]).decode()
    assert "EDITED BY HUMAN" in committed and "is supported on the Enterprise plan" not in committed
    await session.refresh(exp)
    assert "EDITED BY HUMAN" in json.dumps(exp.approved_change)
    assert "EDITED BY HUMAN" not in json.dumps(exp.proposed_change)


async def test_github_failure_is_typed_and_leaves_no_false_success(session, setup, mock_http):
    inc, iv, exp = setup
    await approve(session, iv, inc)
    await ledger.attach_approval(session, exp, (await session.execute(select(Approval))).scalar_one())
    await session.commit()
    mock_github(mock_http, fail_refs=True)
    result, row = await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg()))
    await session.commit()
    assert result.status == ExecutionStatus.FAILED and result.error
    assert result.pr_url is None and not result.commits
    assert row.status == "failed" and row.reference is None
    await session.refresh(exp)
    assert exp.status != ExperimentStatus.AWAITING_VERIFICATION
    assert exp.executed_at is None
    ap = (await session.execute(select(Approval))).scalar_one()
    assert ap.status == ApprovalStatus.APPROVED, "a failed execution must not touch the approval record"


async def test_live_without_credentials_is_refused_not_faked(session, setup, mock_http):
    inc, iv, _ = setup
    await approve(session, iv, inc)
    result, _ = await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=cfg(github_token="")))
    assert result.status == ExecutionStatus.FAILED and result.error_code == "github_not_configured"
    assert len(mock_http.calls) == 0


async def test_executor_authorize_requires_explicit_human_actor():
    """authorize() must reject an approval whose actor type is unknown (None), same as require_executable_approval."""
    import uuid
    from types import SimpleNamespace

    from app.interventions.executor import authorize

    iv = SimpleNamespace(id=uuid.uuid4(), incident_id=uuid.uuid4(), action="update_existing_page",
                         proposed_change=change())
    ap = SimpleNamespace(id=uuid.uuid4(), intervention_id=iv.id, status="approved", decided_by="x",
                         decided_actor_type=None, modified_change=None)
    with pytest.raises(ExecutionRefused):
        authorize(iv, ap)


async def test_http_client_unmocked_calls_fail_loudly(mock_http):
    with pytest.raises(Exception):
        async with httpx.AsyncClient() as c:
            await c.get("https://api.github.com/repos/acme/site")


# ---- pipeline.execute (A14 orchestration) must use the real executor result ----------------------------------------


async def test_pipeline_execute_default_is_manual_package_and_not_dry_run(session, setup, mock_http, emit):
    """No executor choice: pipeline.execute issues the manual package (a REAL execution awaiting a human)."""
    from app.services import pipeline

    inc, iv, exp = setup
    await approve(session, iv, inc)
    out = await pipeline.execute(session, iv.id)
    await session.commit()
    assert len(mock_http.calls) == 0
    assert out["status"] == "awaiting_human_execution" and out["executor"] == "manual", out
    ex = (await session.execute(select(Execution))).scalars().one()
    assert ex.dry_run is False and ex.status == "awaiting_human_execution" and ex.package
    await session.refresh(exp)
    await session.refresh(inc)
    assert exp.status == ExperimentStatus.APPROVED and exp.dry_run is False
    assert inc.state == IncidentState.APPROVED.value  # stays approved until a human records the execution
    again = await pipeline.execute(session, iv.id)  # idempotent: no second package
    assert again["status"] == "awaiting_human_execution"
    assert len((await session.execute(select(Execution))).scalars().all()) == 1


async def test_pipeline_execute_explicit_github_dry_run_is_recorded_as_dry_run(session, setup, mock_http, emit):
    """The GitHub preview (explicit) is the only dry-run, and it is never verified."""
    from app.services import pipeline

    inc, iv, exp = setup
    await approve(session, iv, inc)
    out = await pipeline.execute(session, iv.id, executor="github", dry_run=True)
    await session.commit()
    assert len(mock_http.calls) == 0
    assert out["status"] == "executed" and out["dry_run"] is True, out
    await session.refresh(exp)
    assert exp.dry_run is True and exp.status != ExperimentStatus.AWAITING_VERIFICATION
    assert exp.status != ExperimentStatus.FAILED
