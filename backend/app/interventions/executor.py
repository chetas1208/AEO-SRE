"""Executor interface + the OPTIONAL GitHub PR executor. The default executor is `app.interventions.manual`.

Safety invariants (tested):
- nothing executes without an APPROVED/MODIFIED approval decided by a human for THIS intervention
  (the only exception is `observe`, which never mutates anything);
- dry-run performs zero network mutations and by default zero network calls; it records the exact planned calls;
- PRs are never merged; branch/PR creation is idempotent so a retry after a partial failure is safe;
- a failure yields a typed ExecutionResult(status=failed, retryable=...) and never touches approval state.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import httpx
import structlog
from pydantic import BaseModel, Field

from app.connectors.github.client import (
    GitHubClient,
    GitHubError,
    GitHubResponse,
    decode_content,
    encode_content,
)
from app.core.config import Settings, get_settings
from app.domain.enums import ActionType, ApprovalStatus, IncidentState
from app.interventions.changes import (
    PAGE_KINDS,
    FileChange,
    ProposedChange,
    apply_change,
    diff_file,
    validate_change,
)
from app.interventions.templates import slugify
from app.services.approvals import effective_change

log = structlog.get_logger()
GITHUB_ACTIONS = frozenset({
    ActionType.UPDATE_EXISTING_PAGE, ActionType.CREATE_FAQ, ActionType.CREATE_CANONICAL_PAGE,
    ActionType.CREATE_COMPARISON_CONTENT,
})
GRANTED = frozenset({ApprovalStatus.APPROVED.value, ApprovalStatus.MODIFIED.value})
EXECUTABLE_INCIDENT_STATES = frozenset({IncidentState.APPROVED.value, IncidentState.EXECUTING.value})


class ExecutionStatus(StrEnum):
    PLANNED = "planned"  # GitHub dry-run preview: nothing was changed anywhere
    AWAITING_HUMAN_EXECUTION = "awaiting_human_execution"  # manual package issued; a human applies it
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ExecutionRefused(Exception):
    """Raised BEFORE any side effect when authorization or preconditions are not met."""

    def __init__(self, message: str, code: str = "not_authorized") -> None:
        super().__init__(message)
        self.code = code


class ApiCall(BaseModel):
    method: str
    path: str
    purpose: str
    mutating: bool
    executed: bool = False
    status: int | None = None
    body: dict[str, Any] | None = None
    params: dict[str, Any] | None = None


class ExecutionResult(BaseModel):
    status: ExecutionStatus
    executor: str
    dry_run: bool
    external_mutation: bool = False
    reference: str | None = None
    branch: str | None = None
    pr_number: int | None = None
    pr_url: str | None = None
    commits: list[str] = Field(default_factory=list)
    diff: str = ""
    api_calls: list[ApiCall] = Field(default_factory=list)
    artifact: dict[str, Any] | None = None
    package: dict[str, Any] | None = None  # manual intervention package
    error: str | None = None
    error_code: str | None = None
    retryable: bool = False
    reused: list[str] = Field(default_factory=list)  # e.g. ["branch", "pull_request"] on idempotent retry
    approval_status: str | None = None
    approver: str | None = None
    log: list[str] = Field(default_factory=list)
    started_at: datetime
    finished_at: datetime

    @property
    def planned_calls(self) -> list[ApiCall]:
        return [c for c in self.api_calls if not c.executed]

    @property
    def mutating_calls(self) -> list[ApiCall]:
        return [c for c in self.api_calls if c.mutating]

    def to_log(self) -> list[dict[str, Any]]:
        return [{"line": line} for line in self.log] + [{"api_call": c.model_dump(mode="json")} for c in self.api_calls]


class ExecutorCapability(BaseModel):
    """What an executor can do right now. `state` is healthy | unavailable (an unconfigured OPTIONAL executor is
    unavailable, never an alarm)."""

    name: str
    label: str
    state: str
    available: bool
    mutates_external: bool
    default: bool = False
    detail: str = ""


@runtime_checkable
class InterventionExecutor(Protocol):
    """Execution is an interface. `execute(intervention, approval) -> ExecutionResult` (+ optional context)."""

    name: str

    async def execute(self, intervention: Any, approval: Any | None, incident: Any | None = None, *,
                      evidence: list[Any] | None = None, policy_version: str | None = None) -> ExecutionResult: ...

    def capability(self) -> ExecutorCapability: ...


Executor = InterventionExecutor  # backwards-compatible alias


def _v(x: Any) -> str:
    return str(getattr(x, "value", x))


def check_gate(intervention: Any, approval: Any | None, incident: Any | None = None) -> None:
    """Approval + lifecycle gate shared by every executor (`observe` is exempt). Raises ExecutionRefused."""
    action = ActionType(_v(intervention.action))
    if action is not ActionType.OBSERVE:
        if approval is None:
            raise ExecutionRefused("no approval object supplied: human approval is required before execution",
                                   "approval_required")
        if _v(approval.status) not in GRANTED:
            raise ExecutionRefused(f"approval is '{_v(approval.status)}', not approved/modified", "approval_not_granted")
        if str(approval.intervention_id) != str(intervention.id):
            raise ExecutionRefused("approval belongs to a different intervention", "approval_mismatch")
        if not getattr(approval, "decided_by", None):
            raise ExecutionRefused("approval has no recorded approver", "approval_no_approver")
        if getattr(approval, "decided_actor_type", None) != "human":
            raise ExecutionRefused("approval was not decided by a human", "approval_not_human")
        if incident is not None and _v(incident.state) not in EXECUTABLE_INCIDENT_STATES:
            raise ExecutionRefused(f"incident is '{_v(incident.state)}'; execution requires an approved incident",
                                   "incident_state")


def authorize(intervention: Any, approval: Any | None, incident: Any | None = None) -> tuple[ProposedChange, Any | None]:
    """Gate + validated effective change (human modification wins). Raises ExecutionRefused."""
    check_gate(intervention, approval, incident)
    raw = effective_change(approval, intervention)  # human modification wins when MODIFIED
    if not raw or not isinstance(raw, dict) or not raw.get("kind"):
        raise ExecutionRefused("this intervention has no executable proposed change", "no_proposed_change")
    try:
        change = ProposedChange.model_validate(raw)
    except Exception as exc:
        raise ExecutionRefused(f"proposed change is malformed: {exc}", "invalid_change") from exc
    problems = validate_change(change)
    if problems:
        raise ExecutionRefused("proposed change failed safety validation: " + "; ".join(problems), "unsafe_change")
    return change, approval


def branch_name(incident_number: int | str, intervention: Any) -> str:
    slug = f"{slugify(_v(intervention.action), max_len=30)}-{str(intervention.id).replace('-', '')[:6]}"
    return f"aeo-sre/{incident_number}-{slug}"


def build_pr_body(*, intervention: Any, incident: Any | None, change: ProposedChange, approval: Any,
                  policy_version: str | None, evidence: list[Any] | None, branch: str, app_base_url: str) -> str:
    inc_ref = "n/a"
    if incident is not None:
        link = f"{app_base_url.rstrip('/')}/incidents/{incident.id}"
        inc_ref = f"[Incident #{getattr(incident, 'number', '?')}: {incident.title}]({link})"
    ev_lines = []
    for e in (evidence or [])[:8]:
        def field(name: str, item: Any = e) -> Any:
            return item.get(name) if isinstance(item, dict) else getattr(item, name, None)

        ev_lines.append(
            f"- {field('title') or field('url') or field('id')} "
            f"[{_v(field('type'))}/{_v(field('status'))}] {field('url') or ''}".rstrip()
        )
    when = getattr(approval, "decided_at", None)
    stamp = f", {when.isoformat()}" if hasattr(when, "isoformat") else ""
    lines = [
        "## Profound Lift proposed change", "",
        f"**Incident:** {inc_ref}", f"**Action:** `{_v(intervention.action)}` (risk: {_v(intervention.risk)})",
        f"**Policy version:** {policy_version or 'unknown'}"
        + (f" (score {intervention.score:.2f})" if getattr(intervention, "score", None) is not None else ""),
        f"**Approved by:** {approval.decided_by} ({_v(approval.status)}{stamp})",
        *([f"**Approver note:** {approval.note}"] if getattr(approval, "note", None) else []),
        "", "### Why", intervention.rationale or change.summary, "", "### Summary of change", change.summary or "",
        "", "### Evidence", *(ev_lines or ["- (no evidence summary supplied)"]),
        *( ["", "Cited fact ids: " + ", ".join(change.fact_ids)] if change.fact_ids else [] ),
        "", "### Review notes",
        "- Content is generated only from the cited evidence; verify each statement before merging.",
        "- This PR was NOT merged automatically and will never be. A human must review and merge.",
        f"- Rollback: close this PR and delete branch `{branch}`.",
        *[f"- {n}" for n in change.notes],
    ]
    return "\n".join(lines) + "\n"


class _Run:
    def __init__(self, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.calls: list[ApiCall] = []
        self.log: list[str] = []
        self.commits: list[str] = []
        self.diffs: list[str] = []
        self.reused: list[str] = []
        self.branch: str | None = None
        self.pr_number: int | None = None
        self.pr_url: str | None = None
        self.mutated = False


class GitHubPRExecutor:
    """branch -> commits -> PR over the GitHub REST API. `dry_run=True` (default) never touches the network."""

    name = "github_pr"

    def capability(self) -> ExecutorCapability:
        ok = self.configured
        return ExecutorCapability(
            name=self.name, label="GitHub PR (optional)", state="healthy" if ok else "unavailable", available=ok,
            mutates_external=True,
            detail="opens a PR (never merges)" if ok else "optional; not configured (GITHUB_TOKEN/OWNER/REPO)")

    def __init__(self, *, dry_run: bool = True, read_in_dry_run: bool = False, draft: bool = False,
                 settings: Settings | None = None, client: GitHubClient | None = None,
                 http_client: httpx.AsyncClient | None = None, base_url: str | None = None) -> None:
        self.dry_run = dry_run
        self.read_in_dry_run = read_in_dry_run
        self.draft = draft
        self.settings = settings or get_settings()
        self._client = client
        self._http = http_client
        self._base_url = base_url

    @property
    def configured(self) -> bool:
        s = self.settings
        return bool(s.github_token and s.github_owner and s.github_repo)

    def _gh(self) -> GitHubClient:
        if self._client is None:
            s = self.settings
            kw = {"base_url": self._base_url} if self._base_url else {}
            self._client = GitHubClient(token=s.github_token, owner=s.github_owner or "OWNER",
                                        repo=s.github_repo or "REPO", http_client=self._http, **kw)
        return self._client

    async def _call(self, run: _Run, method: str, path: str, purpose: str, *, mutating: bool,
                    body: dict | None = None, params: dict | None = None,
                    dry_value: GitHubResponse | None = None) -> GitHubResponse:
        rec = ApiCall(method=method, path=path, purpose=purpose, mutating=mutating, body=body, params=params)
        run.calls.append(rec)
        if run.dry_run and (mutating or not self.read_in_dry_run):
            return dry_value if dry_value is not None else GitHubResponse(200, None)
        resp = await self._gh().request(method, path, json=body, params=params)
        rec.executed, rec.status = True, resp.status
        if mutating and resp.ok:
            run.mutated = True
        return resp

    # ---- main entrypoint ----------------------------------------------------------------------------
    async def execute(self, intervention: Any, approval: Any | None, incident: Any | None = None, *,
                      evidence: list[Any] | None = None, policy_version: str | None = None) -> ExecutionResult:
        change, approval = authorize(intervention, approval, incident)  # raises ExecutionRefused: zero side effects
        started = datetime.now(UTC)
        if ActionType(_v(intervention.action)) not in GITHUB_ACTIONS or change.kind not in PAGE_KINDS:
            raise ExecutionRefused("GitHubPRExecutor cannot execute this action; use the manual executor",
                                   "wrong_executor")
        run = _Run(self.dry_run)
        number = getattr(incident, "number", None) or str(intervention.incident_id)[:8]
        branch = run.branch = branch_name(number, intervention)
        if not self.dry_run and not self.configured:
            return self._result(run, started, ExecutionStatus.FAILED, approval, change,
                                error="GitHub is not configured (GITHUB_TOKEN/OWNER/REPO); refusing live execution",
                                code="github_not_configured", retryable=False)
        try:
            await self._run(run, intervention, incident, change, approval, evidence, policy_version, branch)
        except GitHubError as exc:
            log.warning("executor.github_failed", branch=branch, error=str(exc), retryable=exc.retryable)
            run.log.append(f"FAILED: {exc} (retryable={exc.retryable}); approval and incident state unchanged")
            return self._result(run, started, ExecutionStatus.FAILED, approval, change, error=str(exc),
                                code=exc.code, retryable=exc.retryable)
        except _Stop as stop:
            run.log.append(f"FAILED: {stop.message}")
            return self._result(run, started, ExecutionStatus.FAILED, approval, change, error=stop.message,
                                code=stop.code, retryable=stop.retryable)
        status = ExecutionStatus.PLANNED if self.dry_run else ExecutionStatus.SUCCEEDED
        return self._result(run, started, status, approval, change)

    def _result(self, run: _Run, started: datetime, status: ExecutionStatus, approval: Any, change: ProposedChange,
                *, error: str | None = None, code: str | None = None, retryable: bool = False) -> ExecutionResult:
        return ExecutionResult(
            status=status, executor=self.name, dry_run=self.dry_run, external_mutation=run.mutated,
            reference=run.pr_url, branch=run.branch, pr_number=run.pr_number, pr_url=run.pr_url, commits=run.commits,
            diff="".join(run.diffs), api_calls=run.calls, error=error, error_code=code, retryable=retryable,
            reused=run.reused, approval_status=_v(approval.status) if approval is not None else None,
            approver=getattr(approval, "decided_by", None), log=run.log, started_at=started,
            finished_at=datetime.now(UTC))

    async def _run(self, run: _Run, intervention: Any, incident: Any | None, change: ProposedChange, approval: Any,
                   evidence: list[Any] | None, policy_version: str | None, branch: str) -> None:
        s, gh = self.settings, self._gh()
        base = s.github_base_branch or "main"
        mode = "DRY-RUN (no mutation)" if self.dry_run else "LIVE"
        run.log.append(f"{mode}: branch {branch} from {base}; {len(change.files)} file(s); approver "
                       f"{approval.decided_by} ({_v(approval.status)})")
        # 1. base branch head
        r = await self._call(run, "GET", gh.ref_path(base), "resolve base branch head", mutating=False,
                             dry_value=GitHubResponse(200, {"object": {"sha": "<base_head_sha>"}}))
        if r.status != 200:
            raise _Stop(f"base branch '{base}' not found", "base_branch_not_found")
        base_sha = r.data["object"]["sha"]
        # 2. existing PR for this branch (idempotency)
        r = await self._call(run, "GET", f"{gh.repo_path}/pulls", "detect existing pull request", mutating=False,
                             params={"head": f"{gh.owner}:{branch}", "state": "all", "per_page": 10},
                             dry_value=GitHubResponse(200, []))
        existing_pr = None
        if r.status == 200 and isinstance(r.data, list) and r.data:
            existing_pr = min(r.data, key=lambda p: (p.get("state") != "open", -p.get("number", 0)))
            if existing_pr.get("merged_at"):
                run.pr_number, run.pr_url = existing_pr["number"], existing_pr["html_url"]
                run.reused.append("pull_request")
                run.log.append(f"PR #{run.pr_number} for this branch was already merged by a human; nothing to do")
                return
            if existing_pr.get("state") == "open":
                run.reused.append("pull_request")
                run.log.append(f"found open PR #{existing_pr['number']}; reusing")
        # 3. branch
        r = await self._call(run, "GET", gh.ref_path(branch), "check whether branch exists", mutating=False,
                             dry_value=GitHubResponse(404, None))
        if r.status == 200:
            run.reused.append("branch")
            run.log.append("branch already exists; reusing")
        else:
            r = await self._call(run, "POST", f"{gh.repo_path}/git/refs", "create branch", mutating=True,
                                 body={"ref": f"refs/heads/{branch}", "sha": base_sha},
                                 dry_value=GitHubResponse(201, None))
            if r.status == 422:
                run.reused.append("branch")
            elif not r.ok:
                raise _Stop(f"could not create branch: {_msg(r)}", "branch_create_failed", retryable=False)
        # 4. files -> commits
        pr_title = f"[Profound Lift] {change.title or intervention.title}"[:250]
        for fc in change.files:
            await self._commit_file(run, gh, fc, branch, pr_title, approval, incident)
        # 5. pull request (never merged)
        if existing_pr is not None and existing_pr.get("state") == "open":
            run.pr_number, run.pr_url = existing_pr["number"], existing_pr["html_url"]
            return
        body = build_pr_body(intervention=intervention, incident=incident, change=change, approval=approval,
                             policy_version=policy_version, evidence=evidence, branch=branch,
                             app_base_url=s.app_base_url)
        r = await self._call(run, "POST", f"{gh.repo_path}/pulls", "open pull request (human merges)", mutating=True,
                             body={"title": pr_title, "head": branch, "base": base, "body": body, "draft": self.draft},
                             dry_value=GitHubResponse(201, {"number": None, "html_url": None}))
        if self.dry_run:
            run.log.append("planned: open PR; never merged automatically")
            return
        if r.status == 201:
            run.pr_number, run.pr_url = r.data["number"], r.data["html_url"]
            run.log.append(f"opened PR #{run.pr_number}: {run.pr_url}")
        elif r.status == 422:
            found = await gh.find_pr(branch)
            msg = _msg(r)
            if found and found.get("state") == "open":
                run.pr_number, run.pr_url = found["number"], found["html_url"]
                run.reused.append("pull_request")
            elif "no commits between" in msg.lower():
                raise _Stop("the change is identical to the base branch; nothing to propose", "no_effective_change")
            else:
                raise _Stop(f"GitHub rejected the PR: {msg}", "pr_create_failed")
        else:
            raise _Stop(f"unexpected PR response {r.status}: {_msg(r)}", "pr_create_failed", retryable=True)

    async def _commit_file(self, run: _Run, gh: GitHubClient, fc: FileChange, branch: str, title: str, approval: Any,
                           incident: Any | None) -> None:
        r = await self._call(run, "GET", gh.contents_path(fc.path), f"read {fc.path} on branch", mutating=False,
                             params={"ref": branch}, dry_value=GitHubResponse(404, None))
        current: str | None = fc.old_content
        sha: str | None = "<file_sha_if_exists>" if fc.old_content is not None else None
        if not (run.dry_run and not self.read_in_dry_run):
            current, sha = None, None
            if r.status == 200 and isinstance(r.data, dict):
                current, sha = decode_content(r.data.get("content", "")), r.data["sha"]
        final = apply_change(current, fc)
        run.diffs.append(diff_file(fc.model_copy(update={"old_content": current})))
        if current is not None and final == current and not run.dry_run:
            run.log.append(f"{fc.path}: already up to date on branch (idempotent skip)")
            return
        body: dict[str, Any] = {
            "message": f"aeo-sre: {title}\n\nApproved-by: {approval.decided_by}"
                       f"\nIncident: {getattr(incident, 'number', 'n/a')}",
            "content": encode_content(final), "branch": branch}
        if sha:
            body["sha"] = sha
        r = await self._call(run, "PUT", gh.contents_path(fc.path), f"commit {fc.path}", mutating=True, body=body,
                             dry_value=GitHubResponse(200, None))
        if run.dry_run:
            return
        if r.status in (200, 201):
            run.commits.append((r.data or {}).get("commit", {}).get("sha", ""))
            run.log.append(f"committed {fc.path}")
        elif r.status in (409, 422):
            raise _Stop(f"commit of {fc.path} rejected ({r.status}): {_msg(r)}", "commit_conflict",
                        retryable=r.status == 409)
        else:
            raise _Stop(f"unexpected commit response {r.status}", "commit_failed", retryable=True)

    # ---- rollback ----------------------------------------------------------------------------------
    async def rollback(self, *, branch: str, pr_number: int | None = None, actor: str,
                       delete_branch: bool = True) -> ExecutionResult:
        """Close the PR and delete the branch. Never merges or reverts a merged PR (that stays a human action)."""
        if not actor:
            raise ExecutionRefused("rollback requires an actor", "actor_required")
        if not branch.startswith("aeo-sre/"):
            raise ExecutionRefused("refusing to delete a branch outside the aeo-sre/ namespace", "bad_branch")
        started = datetime.now(UTC)
        run = _Run(self.dry_run)
        run.branch, run.pr_number = branch, pr_number
        if not self.dry_run and not self.configured:
            return self._rb_result(run, started, error="GitHub is not configured", code="github_not_configured")
        gh = self._gh()
        try:
            if pr_number is not None:
                r = await self._call(run, "PATCH", f"{gh.repo_path}/pulls/{pr_number}", "close pull request",
                                     mutating=True, body={"state": "closed"})
                run.log.append(f"close PR #{pr_number}: {r.status}")
            if delete_branch:
                r = await self._call(run, "DELETE", f"{gh.repo_path}/git/refs/heads/{branch}", "delete branch",
                                     mutating=True)
                run.log.append(f"delete branch {branch}: {r.status}" + (" (already gone)" if r.status in (404, 422) else ""))
        except GitHubError as exc:
            return self._rb_result(run, started, error=str(exc), code=exc.code, retryable=exc.retryable)
        run.log.append(f"rollback by {actor}")
        return self._rb_result(run, started)

    def _rb_result(self, run: _Run, started: datetime, *, error: str | None = None, code: str | None = None,
                   retryable: bool = False) -> ExecutionResult:
        status = ExecutionStatus.FAILED if error else (ExecutionStatus.PLANNED if self.dry_run
                                                       else ExecutionStatus.SUCCEEDED)
        return ExecutionResult(status=status, executor=self.name, dry_run=self.dry_run, external_mutation=run.mutated,
                               branch=run.branch, pr_number=run.pr_number, api_calls=run.calls, log=run.log,
                               error=error, error_code=code, retryable=retryable, started_at=started,
                               finished_at=datetime.now(UTC))


class _Stop(Exception):
    def __init__(self, message: str, code: str, retryable: bool = False) -> None:
        super().__init__(message)
        self.message, self.code, self.retryable = message, code, retryable


def _msg(r: GitHubResponse) -> str:
    if isinstance(r.data, dict):
        errs = "; ".join(str(e.get("message", e)) if isinstance(e, dict) else str(e) for e in r.data.get("errors", []))
        return f"{r.data.get('message', '')} {errs}".strip()
    return str(r.status)


EXECUTOR_ALIASES = {"manual": "manual", "github": "github_pr", "github_pr": "github_pr",
                    "profound_agent": "profound_agent", "profound": "profound_agent"}


def normalize_executor_choice(choice: str | None) -> str:
    """None/'' -> 'manual' (the default for ALL actions). Raises ExecutionRefused on an unknown name."""
    if choice is None or not str(choice).strip():
        return "manual"
    key = EXECUTOR_ALIASES.get(str(choice).strip().lower())
    if key is None:
        raise ExecutionRefused(f"unknown executor {choice!r}; choose one of: manual, github, profound_agent",
                               "unknown_executor")
    return key


def github_configured(settings: Settings | None = None) -> bool:
    st = settings or get_settings()
    return bool(st.github_token and st.github_owner and st.github_repo)


def select_executor(action: ActionType | str, *, choice: str | None = None, dry_run: bool | None = None,
                    settings: Settings | None = None, **kw: Any) -> Executor:
    """DEFAULT: ManualExecutor for every action (always available, no credentials).

    GitHubPRExecutor is used ONLY when the caller explicitly asks for it (`choice="github"`) AND it is configured
    (the one exception: an explicit `dry_run=True` preview, which never executes and never counts for learning).
    There is no silent fall-back to a GitHub dry-run: an explicit-but-unavailable choice is refused.
    """
    from app.interventions.manual import ManualExecutor, ProfoundAgentExecutor

    action = ActionType(_v(action))
    which = normalize_executor_choice(choice)
    if which == "manual":
        return ManualExecutor()
    if which == "profound_agent":
        return ProfoundAgentExecutor(settings=get_settings())
    if action not in GITHUB_ACTIONS:
        raise ExecutionRefused(f"the GitHub executor cannot execute {action.value}; use the manual executor",
                               "wrong_executor")
    st = settings or get_settings()
    configured = github_configured(st)
    if not configured and dry_run is not True:
        raise ExecutionRefused("the GitHub executor is not configured (optional: GITHUB_TOKEN/OWNER/REPO)",
                               "github_not_configured")
    return GitHubPRExecutor(dry_run=bool(dry_run) if dry_run is not None else False, settings=st, **kw)
