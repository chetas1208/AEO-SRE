"""Executor tests. The default is ManualExecutor; the GitHub executor tests (respx-mocked, no real network) are
OPTIONAL/SECONDARY and run without any GITHUB_* environment (credentials are injected via explicit Settings)."""
from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from app.core.config import Settings
from app.interventions.changes import FileChange, ManualTask, ProposedChange, wrap_section
from app.interventions.executor import (
    ExecutionRefused,
    ExecutionStatus,
    GitHubPRExecutor,
    authorize,
    branch_name,
    select_executor,
)
from app.interventions.manual import ManualExecutor, ProfoundAgentExecutor

API = "https://api.github.com"
REPO = f"{API}/repos/acme/site"


def settings(**kw: Any) -> Settings:
    base = dict(github_token="ghp_test", github_owner="acme", github_repo="site", github_base_branch="main",
                app_base_url="https://aeo.example")
    return Settings(_env_file=None, **{**base, **kw})


def make_change(path: str = "content/enterprise/security.md") -> ProposedChange:
    section = wrap_section("incident-12", "## SAML SSO\n\n- SAML SSO is supported on the Enterprise plan.")
    return ProposedChange(
        title="Add SAML SSO section", target_url="https://acme.example/enterprise/security", summary="Add section",
        files=[FileChange(path=path, old_content="# Security\n", new_content=section, change_type="update",
                          patch_mode="upsert_section", section_id="incident-12")],
        fact_ids=["e1#0"])


def make_iv(change: ProposedChange | dict | None = None, action: str = "update_existing_page") -> SimpleNamespace:
    pc = change if isinstance(change, dict) else (change or make_change()).to_json()
    return SimpleNamespace(id=uuid.uuid4(), incident_id=uuid.uuid4(), action=action, title="Update page",
                           rationale="SAML content is missing.", risk="low", proposed_change=pc, score=0.71,
                           policy_version_id=None)


def make_approval(iv: SimpleNamespace, status: str = "approved", **kw: Any) -> SimpleNamespace:
    d = dict(id=uuid.uuid4(), intervention_id=iv.id, status=status, decided_by="alice@acme.example",
             decided_actor_type="human", decided_at=None, note="ship it", modified_change=None)
    return SimpleNamespace(**{**d, **kw})


def make_incident(iv: SimpleNamespace, state: str = "approved") -> SimpleNamespace:
    return SimpleNamespace(id=iv.incident_id, number=12, title="Lost SSO visibility", state=state)


def mock_happy(router: respx.MockRouter, branch: str, *, branch_exists=False, pr: list | None = None,
               file_status: int = 404, file_content: str = "# Security\n"):
    import base64

    router.get(f"{REPO}/git/ref/heads/main").respond(200, json={"object": {"sha": "basesha"}})
    router.get(f"{REPO}/pulls").respond(200, json=pr or [])
    if branch_exists:
        router.get(f"{REPO}/git/ref/heads/{branch}").respond(200, json={"object": {"sha": "basesha"}})
    else:
        router.get(f"{REPO}/git/ref/heads/{branch}").respond(404, json={"message": "Not Found"})
    refs = router.post(f"{REPO}/git/refs").respond(201, json={})
    if file_status == 200:
        router.get(url__regex=rf"{REPO}/contents/.*").respond(200, json={
            "sha": "filesha", "content": base64.b64encode(file_content.encode()).decode()})
    else:
        router.get(url__regex=rf"{REPO}/contents/.*").respond(404, json={"message": "Not Found"})
    put = router.put(url__regex=rf"{REPO}/contents/.*").respond(201, json={"commit": {"sha": "commit1"}})
    pulls = router.post(f"{REPO}/pulls").respond(201, json={"number": 7, "html_url": "https://github.com/acme/site/pull/7"})
    return refs, put, pulls


@pytest.fixture
def router():
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as r:
        yield r


# ---- authorization ------------------------------------------------------------------------------------
@pytest.mark.parametrize("make", [
    lambda iv: None,
    lambda iv: make_approval(iv, "pending"),
    lambda iv: make_approval(iv, "rejected"),
    lambda iv: make_approval(iv, "expired"),
    lambda iv: make_approval(iv, "approved", intervention_id=uuid.uuid4()),
    lambda iv: make_approval(iv, "approved", decided_actor_type="model"),
    lambda iv: make_approval(iv, "approved", decided_by=None),
], ids=["none", "pending", "rejected", "expired", "other-intervention", "model-actor", "no-approver"])
async def test_refuses_without_valid_approval(router, make):
    iv = make_iv()
    ex = GitHubPRExecutor(dry_run=False, settings=settings())
    with pytest.raises(ExecutionRefused):
        await ex.execute(iv, make(iv), make_incident(iv))
    assert len(router.calls) == 0  # fail closed: no HTTP at all


async def test_refuses_when_incident_not_approved(router):
    iv = make_iv()
    ex = GitHubPRExecutor(dry_run=False, settings=settings())
    with pytest.raises(ExecutionRefused) as e:
        await ex.execute(iv, make_approval(iv), make_incident(iv, state="awaiting_approval"))
    assert e.value.code == "incident_state"
    assert len(router.calls) == 0


async def test_refuses_without_proposed_change_and_unsafe_paths(router):
    iv = make_iv(change={})
    with pytest.raises(ExecutionRefused) as e:
        authorize(iv, make_approval(iv))
    assert e.value.code == "no_proposed_change"
    bad = make_iv(make_change(".github/workflows/ci.yml"))
    with pytest.raises(ExecutionRefused) as e:
        authorize(bad, make_approval(bad))
    assert e.value.code == "unsafe_change"
    assert len(router.calls) == 0


# ---- dry run ------------------------------------------------------------------------------------------
async def test_dry_run_makes_zero_http_calls_and_records_plan(router):
    iv = make_iv()
    ex = GitHubPRExecutor(dry_run=True, settings=settings())  # default
    res = await ex.execute(iv, make_approval(iv), make_incident(iv), policy_version="v0.0.3")
    assert len(router.calls) == 0
    assert res.status is ExecutionStatus.PLANNED and res.dry_run and not res.external_mutation
    methods = [(c.method, c.purpose) for c in res.api_calls]
    assert ("POST", "create branch") in methods and ("POST", "open pull request (human merges)") in methods
    assert any(c.method == "PUT" and "contents" in c.path for c in res.api_calls)
    assert all(not c.executed for c in res.api_calls)
    assert res.branch == f"aeo-sre/12-update-existing-page-{str(iv.id).replace('-', '')[:6]}"
    assert "+- SAML SSO is supported on the Enterprise plan." in res.diff
    pr_call = next(c for c in res.api_calls if c.path.endswith("/pulls") and c.method == "POST")
    assert "v0.0.3" in pr_call.body["body"] and "alice@acme.example" in pr_call.body["body"]
    assert "https://aeo.example/incidents/" in pr_call.body["body"]


async def test_dry_run_without_credentials_still_plans(router):
    iv = make_iv()
    res = await GitHubPRExecutor(settings=settings(github_token="", github_owner="", github_repo="")).execute(
        iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.PLANNED and len(router.calls) == 0


async def test_dry_run_with_reads_only_issues_get(router):
    iv = make_iv()
    branch = branch_name(12, iv)
    mock_happy(router, branch)
    ex = GitHubPRExecutor(dry_run=True, read_in_dry_run=True, settings=settings())
    res = await ex.execute(iv, make_approval(iv), make_incident(iv))
    assert len(router.calls) > 0
    assert {c.request.method for c in router.calls} == {"GET"}
    assert res.status is ExecutionStatus.PLANNED and not res.external_mutation


def test_select_executor_defaults_to_manual_for_every_action():
    for a in ("update_existing_page", "create_faq", "create_canonical_page", "create_comparison_content",
              "publisher_outreach", "structured_data", "observe"):
        assert isinstance(select_executor(a, settings=settings(github_token="")), ManualExecutor)
        assert isinstance(select_executor(a, settings=settings()), ManualExecutor)  # even WITH creds: opt-in only


def test_select_executor_github_only_when_explicit_and_configured():
    live = select_executor("create_faq", choice="github", settings=settings())
    assert isinstance(live, GitHubPRExecutor) and not live.dry_run
    with pytest.raises(ExecutionRefused) as e:  # no silent fall-back to a dry-run
        select_executor("create_faq", choice="github", settings=settings(github_token=""))
    assert e.value.code == "github_not_configured"
    assert select_executor("create_faq", choice="github", dry_run=True, settings=settings(github_token="")).dry_run
    with pytest.raises(ExecutionRefused):
        select_executor("publisher_outreach", choice="github", settings=settings())
    with pytest.raises(ExecutionRefused):
        select_executor("create_faq", choice="nope", settings=settings())
    assert isinstance(select_executor("create_faq", choice="profound_agent"), ProfoundAgentExecutor)


def test_executor_capabilities_manual_healthy_others_unavailable_unconfigured():
    from app.interventions.manual import executor_capabilities

    caps = executor_capabilities(settings(github_token=""))
    assert caps["manual"].available and caps["manual"].state == "healthy" and not caps["manual"].mutates_external
    assert not caps["github_pr"].available and caps["github_pr"].state == "unavailable"
    assert not caps["profound_agent"].available and caps["profound_agent"].state == "unavailable"
    assert executor_capabilities(settings())["github_pr"].available


async def test_live_without_config_fails_non_retryable(router):
    iv = make_iv()
    res = await GitHubPRExecutor(dry_run=False, settings=settings(github_token="")).execute(
        iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.FAILED and not res.retryable and res.error_code == "github_not_configured"
    assert len(router.calls) == 0


# ---- live ---------------------------------------------------------------------------------------------
async def test_live_happy_path_branch_commit_pr_never_merge(router):
    iv, appr = make_iv(), None
    appr = make_approval(iv, "approved")
    branch = branch_name(12, iv)
    refs, put, pulls = mock_happy(router, branch)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(
        iv, appr, make_incident(iv), policy_version="v0.1.0",
        evidence=[SimpleNamespace(id="e1", title="Security page", url="https://acme.example/security",
                                  type="owned", status="live")])
    assert res.status is ExecutionStatus.SUCCEEDED and res.external_mutation
    assert res.pr_url == "https://github.com/acme/site/pull/7" and res.reference == res.pr_url
    assert res.commits == ["commit1"] and appr.status == "approved"
    assert refs.call_count == 1 and put.call_count == 1 and pulls.call_count == 1
    ref_body = refs.calls.last.request.read()
    assert b"refs/heads/" + branch.encode() in ref_body and b"basesha" in ref_body
    pr_json = pulls.calls.last.request
    import json

    body = json.loads(pr_json.read())
    assert body["head"] == branch and body["base"] == "main" and body["draft"] is False
    assert "Approved by:** alice@acme.example" in body["body"] and "v0.1.0" in body["body"]
    assert "Security page" in body["body"] and "NOT merged automatically" in body["body"]
    put_body = json.loads(put.calls.last.request.read())
    assert put_body["branch"] == branch and "sha" not in put_body
    assert not any("merge" in str(c.request.url) for c in router.calls)
    assert router.calls[0].request.headers["authorization"] == "Bearer ghp_test"


async def test_idempotent_retry_reuses_branch_and_pr(router):
    iv = make_iv()
    branch = branch_name(12, iv)
    existing = make_change().files[0]
    merged = "# Security\n\n" + existing.new_content
    pr = [{"number": 7, "state": "open", "html_url": "https://github.com/acme/site/pull/7", "merged_at": None}]
    refs, put, pulls = mock_happy(router, branch, branch_exists=True, pr=pr, file_status=200, file_content=merged)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(
        iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.SUCCEEDED
    assert refs.call_count == 0 and put.call_count == 0 and pulls.call_count == 0
    assert set(res.reused) == {"branch", "pull_request"} and res.pr_number == 7


async def test_retry_after_partial_failure_creates_missing_pr_only(router):
    iv = make_iv()
    branch = branch_name(12, iv)
    existing = make_change().files[0]
    merged = "# Security\n\n" + existing.new_content
    refs, put, pulls = mock_happy(router, branch, branch_exists=True, file_status=200, file_content=merged)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert refs.call_count == 0 and put.call_count == 0 and pulls.call_count == 1
    assert res.pr_number == 7


async def test_branch_race_422_is_treated_as_existing(router):
    iv = make_iv()
    branch = branch_name(12, iv)
    refs, put, pulls = mock_happy(router, branch)
    refs.respond(422, json={"message": "Reference already exists"})
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.SUCCEEDED and "branch" in res.reused


@pytest.mark.parametrize(("status", "retryable"), [(500, True), (502, True), (401, False)])
async def test_http_failure_is_typed_and_keeps_approval(router, status, retryable):
    iv = make_iv()
    appr = make_approval(iv)
    branch = branch_name(12, iv)
    _, put, _ = mock_happy(router, branch)
    put.respond(status, json={"message": "boom"})
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, appr, make_incident(iv))
    assert res.status is ExecutionStatus.FAILED and res.retryable is retryable and res.error
    assert appr.status == "approved" and res.approval_status == "approved"
    assert res.branch == branch  # caller can resume/rollback


async def test_rate_limit_is_retryable(router):
    iv = make_iv()
    router.get(f"{REPO}/git/ref/heads/main").respond(403, json={"message": "API rate limit exceeded"},
                                                      headers={"x-ratelimit-remaining": "0"})
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.FAILED and res.retryable and res.error_code == "rate_limited"


async def test_network_error_is_retryable(router):
    iv = make_iv()
    router.get(f"{REPO}/git/ref/heads/main").mock(side_effect=httpx.ConnectError("down"))
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.FAILED and res.retryable and res.error_code == "network_error"


async def test_missing_base_branch_non_retryable(router):
    iv = make_iv()
    router.get(f"{REPO}/git/ref/heads/main").respond(404, json={"message": "Not Found"})
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.FAILED and not res.retryable and res.error_code == "base_branch_not_found"


async def test_modified_approval_executes_modified_change(router):
    iv = make_iv()
    modified = make_change("content/enterprise/sso.md")
    appr = make_approval(iv, "modified", modified_change=modified.to_json())
    branch = branch_name(12, iv)
    _, put, _ = mock_happy(router, branch)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, appr, make_incident(iv))
    assert res.status is ExecutionStatus.SUCCEEDED
    assert "content/enterprise/sso.md" in str(put.calls.last.request.url)


async def test_existing_merged_pr_is_not_recreated(router):
    iv = make_iv()
    branch = branch_name(12, iv)
    pr = [{"number": 3, "state": "closed", "html_url": "https://github.com/acme/site/pull/3", "merged_at": "2026-10-01"}]
    refs, put, pulls = mock_happy(router, branch, pr=pr)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert res.status is ExecutionStatus.SUCCEEDED and res.pr_number == 3
    assert refs.call_count == 0 and put.call_count == 0 and pulls.call_count == 0


# ---- rollback -----------------------------------------------------------------------------------------
async def test_rollback_closes_pr_and_deletes_branch(router):
    patch = router.patch(f"{REPO}/pulls/7").respond(200, json={})
    delete = router.delete(f"{REPO}/git/refs/heads/aeo-sre/12-x").respond(204)
    res = await GitHubPRExecutor(dry_run=False, settings=settings()).rollback(
        branch="aeo-sre/12-x", pr_number=7, actor="alice@acme.example")
    assert res.status is ExecutionStatus.SUCCEEDED and patch.call_count == 1 and delete.call_count == 1
    assert b'"closed"' in patch.calls.last.request.read()
    assert not any("merge" in str(c.request.url) for c in router.calls)


async def test_rollback_dry_run_and_guards(router):
    res = await GitHubPRExecutor(settings=settings()).rollback(branch="aeo-sre/12-x", pr_number=7, actor="a")
    assert res.status is ExecutionStatus.PLANNED and len(router.calls) == 0 and len(res.api_calls) == 2
    with pytest.raises(ExecutionRefused):
        await GitHubPRExecutor(dry_run=False, settings=settings()).rollback(branch="main", actor="a")
    with pytest.raises(ExecutionRefused):
        await GitHubPRExecutor(dry_run=False, settings=settings()).rollback(branch="aeo-sre/1-x", actor="")


# ---- manual tasks -------------------------------------------------------------------------------------
def manual_change(kind: str = "manual_task") -> dict:
    task = ManualTask(kind="publisher_outreach", title="Outreach", body="Hello publisher",
                      recipient="news.example", checklist=["send"])
    return ProposedChange(kind=kind, title="Outreach", summary="draft", manual_task=task).to_json()


async def test_manual_executor_issues_package_without_http_or_mutation(router):
    iv = make_iv(manual_change(), action="publisher_outreach")
    res = await ManualExecutor().execute(iv, make_approval(iv), make_incident(iv), policy_version="v0.0.1")
    assert res.status is ExecutionStatus.AWAITING_HUMAN_EXECUTION and not res.external_mutation
    assert not res.dry_run  # a manual execution is REAL, never a dry-run
    pkg = res.package
    assert pkg["manual_task"]["body"] == "Hello publisher" and pkg["policy_version"] == "v0.0.1"
    assert pkg["steps"] and pkg["rollback"] and pkg["risk"] == "low" and pkg["observation_window"]["delay_hours"]
    assert len(router.calls) == 0


async def test_manual_package_for_page_change_has_exact_text_diff_and_target(router):
    iv = make_iv()
    res = await ManualExecutor().execute(iv, make_approval(iv), make_incident(iv))
    pkg = res.package
    ch = pkg["changes"][0]
    assert ch["path"] == "content/enterprise/security.md" and "SAML SSO is supported" in ch["proposed_text"]
    assert ch["current_content"] == "# Security\n" and ch["current_content_known"]
    assert "+" in pkg["diff"] and pkg["target"]["url"] == "https://acme.example/enterprise/security"
    assert any("Mark as executed" in step for step in pkg["steps"]) and "aeo-sre:incident-12" in pkg["rollback"]
    assert pkg["approved_by"] == "alice@acme.example" and pkg["modified_by_human"] is False


async def test_manual_package_uses_the_humans_modification(router):
    iv = make_iv()
    mod = make_change().model_copy(update={"summary": "edited by human"}).to_json()
    res = await ManualExecutor().execute(iv, make_approval(iv, status="modified", modified_change=mod),
                                         make_incident(iv))
    assert res.package["summary"] == "edited by human" and res.package["modified_by_human"] is True


async def test_manual_executor_requires_human_approval_except_observe(router):
    iv = make_iv(manual_change(), action="publisher_outreach")
    with pytest.raises(ExecutionRefused):
        await ManualExecutor().execute(iv, None, make_incident(iv))
    with pytest.raises(ExecutionRefused):
        await ManualExecutor().execute(iv, make_approval(iv, decided_actor_type="model"), make_incident(iv))
    obs = make_iv(ProposedChange(kind="observe", title="Monitor", summary="watch",
                                 manual_task=ManualTask(kind="observe", title="Monitor")).to_json(), action="observe")
    res = await ManualExecutor().execute(obs, None)
    # observe: no human step -> observing immediately, real (not dry-run)
    assert res.status is ExecutionStatus.SUCCEEDED and res.approver is None and not res.dry_run


async def test_profound_agent_executor_is_an_unavailable_stub():
    iv = make_iv()
    with pytest.raises(ExecutionRefused) as e:
        await ProfoundAgentExecutor().execute(iv, make_approval(iv), make_incident(iv))
    assert e.value.code == "executor_unavailable"


async def test_github_executor_rejects_manual_action(router):
    iv = make_iv(manual_change(), action="publisher_outreach")
    with pytest.raises(ExecutionRefused) as e:
        await GitHubPRExecutor(settings=settings()).execute(iv, make_approval(iv), make_incident(iv))
    assert e.value.code == "wrong_executor"


# ---- service + llm client -----------------------------------------------------------------------------
async def test_github_run_execution_persists_dry_run_row_and_refuses_pending(session, router):
    from app.interventions.service import run_execution
    from app.models.interventions import Execution
    from sqlalchemy import select

    from tests import factories

    org = await factories.make_org(session)
    inc = await factories.make_incident(session, org, state="approved")
    iv = await factories.make_intervention(session, inc, proposed_change=make_change().to_json())
    await factories.make_approval(session, iv, status="pending")
    with pytest.raises(ExecutionRefused):
        await run_execution(session, iv.id)
    assert (await session.execute(select(Execution))).first() is None
    await factories.make_approval(session, iv, status="approved", decided_by="alice@acme.example")
    result, row = await run_execution(session, iv.id, executor=GitHubPRExecutor(settings=settings()))
    await session.commit()
    assert result.status is ExecutionStatus.PLANNED and row.dry_run and row.status == "planned"
    assert row.executor == "github_pr" and row.reference is None and row.approval_id is not None
    assert any("api_call" in line for line in row.log)
    assert len(router.calls) == 0


async def test_run_execution_advances_experiment_dry_run_and_leaves_it_on_retryable_failure(session, router):
    from app.interventions.service import run_execution

    from tests import factories

    org = await factories.make_org(session)
    inc = await factories.make_incident(session, org, state="approved")
    iv = await factories.make_intervention(session, inc, proposed_change=make_change().to_json())
    appr = await factories.make_approval(session, iv, status="approved", decided_by="alice@acme.example")
    exp = await factories.make_experiment(session, inc, iv, status="approved", approval_id=appr.id,
                                          approver="alice@acme.example")
    # live attempt with a retryable GitHub failure: experiment untouched, approval untouched
    router.get(f"{REPO}/git/ref/heads/main").respond(503, json={"message": "unavailable"})
    result, row = await run_execution(session, iv.id, executor=GitHubPRExecutor(dry_run=False, settings=settings()))
    assert result.status is ExecutionStatus.FAILED and result.retryable and row.status == "failed"
    await session.refresh(exp)
    assert exp.status == "approved"
    # retry as dry-run: experiment executed, flagged dry_run, no verification window
    result, row = await run_execution(session, iv.id, executor=GitHubPRExecutor(settings=settings()))
    await session.refresh(exp)
    assert exp.status == "executed" and exp.dry_run and exp.verification_window_start is None
    assert not any(c.request.method != "GET" for c in router.calls)


async def test_run_execution_observe_needs_no_approval(session):
    from app.interventions.service import run_execution

    from tests import factories

    org = await factories.make_org(session)
    inc = await factories.make_incident(session, org)
    obs = ProposedChange(kind="observe", title="Monitor", summary="watch",
                         manual_task=ManualTask(kind="observe", title="Monitor")).to_json()
    iv = await factories.make_intervention(session, inc, action="observe", proposed_change=obs)
    result, row = await run_execution(session, iv.id)
    assert result.status is ExecutionStatus.SUCCEEDED and row.executor == "manual" and not result.external_mutation
    assert row.dry_run is False
