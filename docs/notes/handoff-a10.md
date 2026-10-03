# Handoff A10 (Executor / GitHub)

Reference notes: `docs/notes/slap.md`, `docs/notes/recoil.md`, `docs/notes/third-party-a10.md` (both repos unlicensed/ambiguous -> ideas only, no code copied). Requests: `docs/notes/requests-a10.md`.

## Public interfaces (`app.interventions`)
- `propose_interventions(session, incident, decision, *, evidence=None, llm=None, use_default_llm=True, canonical_facts=None, old_content_loader=None, content_root=None, extension=None, path_resolver=None) -> list[Intervention]`
  - One candidate row per `ActionType` (re-running updates rows in place; rows with a decided approval are never rewritten). Flushes, does not commit.
  - `decision`: `app.policy.Decision`, `PolicyDecision` row, or dict. Score = action probability (falls back to mean/score); selected flag, `selection_basis`, `policy_version_id` (resolved from version string) set.
  - Risk: base per action (observe/update/faq/structured_data low; canonical/comparison/outreach medium), bumped one level each when root cause is not confirmed and when no cited facts back the patch.
  - Rationale cites hypothesis status ("proposed: not yet confirmed" vs confirmed), evidence, policy basis. LLM is called ONLY for the selected action.
  - No patch possible (no grounded facts / no target page / no third-party source) -> `proposed_change = None` (stored as JSON null) and the reason is in `rationale` ("No patch available: ...").
  - Facts: verbatim sentences from LIVE/CHANGED owned/competitor/external evidence (stale/failed/unavailable/inference/profound excluded) plus `incident.context["canonical_facts"]` / `canonical_facts=` (strings or `{text, source_url}`). Capability heading: `incident.context["capability"]` > prompt cluster topic > hypothesis title. Target URL: `incident.context["target_url"]` > first owned evidence URL. Repo path: `incident.context["repo_path"]` > URL path + `.md`.
- `ProposedChange(kind, title, files:[FileChange(path, old_content, new_content, change_type, patch_mode, section_id)], target_url, summary, generated_by, fact_ids, manual_task, notes, diff)`; `build_diff(change_or_files)`; `apply_change(old, file_change)`. `patch_mode="upsert_section"` replaces/appends one `<!-- aeo-sre:begin id=... -->` block at apply time (idempotent; never clobbers other edits). `validate_path/validate_change`: no dot-paths (.github), traversal, only text/markup extensions, no deletes, max 5 files.
- LLM: `LLMClient` protocol (`complete_json(*, system, user, schema)`), `PatchDraft` pydantic (title, summary, files, claims). `default_llm_client()` -> `app.connectors.llm.get_llm_client()`. Draft accepted only if schema-valid, touches exactly the template's files, every claim cites supplied fact ids and is supported, and numbers/URLs/acronyms exist in the evidence corpus; otherwise (or on any LLM error) the deterministic template is used and a note is recorded in `ProposedChange.notes`.
- Executors (`app.interventions.executor`): `Executor` protocol, `GitHubPRExecutor(dry_run=True, read_in_dry_run=False, draft=False, settings=, http_client=)`, `ManualTaskExecutor`, `select_executor(action, dry_run=None)`, `authorize(...)`, `branch_name(...)`, `ExecutionResult`, `ExecutionStatus(planned|succeeded|failed)`, `ExecutionRefused` (raised before any side effect).
  - Gate: APPROVED/MODIFIED approval for this intervention with human approver; incident (if given) in approved/executing; change = `app.services.approvals.effective_change`; `observe` exempt. Fail closed.
  - GitHub flow (REST via httpx, `app.connectors.github.GitHubClient`): resolve base head -> detect existing PR for head -> branch (reuse if present, 422 race tolerated) -> per file GET on branch + merge + PUT contents (skip if identical) -> open PR (body: incident link, rationale, evidence, approver, policy version, rollback). NEVER merges. One commit per file (contents API).
  - Dry-run (default; forced when GitHub unconfigured via `select_executor`): zero network by default; `result.api_calls` holds the exact planned calls (with `executed=False`) plus unified `diff`. `read_in_dry_run=True` issues GETs only.
  - Failure: `ExecutionResult(status=failed, retryable, error_code, branch)`; approval/experiment state untouched; retry is idempotent. Retryable: network, 5xx, rate limit, 409. Live without creds: failed, non-retryable.
  - `GitHubPRExecutor.rollback(branch=, pr_number=, actor=)`: closes PR + deletes `aeo-sre/*` branch (dry-run aware). Does not revert merged PRs.
  - `ManualTaskExecutor`: records `result.artifact` (drafted outreach, FAQPage JSON-LD, observe plan); `external_mutation=False`.
- `app.interventions.service.run_execution(session, intervention_id, dry_run=None, executor=None)`: `require_executable_approval` gate -> executor -> persists `Execution` (status `planned` for dry-run, `succeeded`, `failed`; `dry_run` flag; `log` = lines + api_calls) -> experiment: `begin_execution`+`mark_executed` on success/plan, `fail_experiment` on non-retryable failure, untouched on retryable failure -> audit event. Does NOT transition the Incident.

## Tests
`backend/tests/unit/test_executor.py` (35: refuses without approval variants, dry-run zero HTTP, reads-only dry-run, happy path, idempotent retry, partial-failure retry, 401/5xx/rate-limit/network typed failures, modified approval, rollback, manual executor, service + experiment advancement) and `test_propose.py` (16: all actions, grounding, LLM accept/reject/fallback, re-propose, diff/apply, path safety). All pass; ruff clean.

## Known gaps / notes
- Existing-file content is only known if `old_content_loader` is supplied at propose time (not wired to GitHub by default); the executor always reads the live file on the branch at execution time, so diffs in the UI for `upsert_section` on unknown files show the section as an addition.
- New page paths (canonical/FAQ/comparison) and their `target_url` are proposals derived from the capability name; flagged in `notes`.
- If an experiment already executed as dry-run, a later live execution cannot re-advance it (ledger forbids executed->executing); logged as a warning. A14 should create a fresh experiment/approval flow for a live run after a dry-run.
- Profound Agent executor (Plan 4.8 Executor 2) not implemented.
- Competitor statements in comparison content are quoted verbatim with source and retrieval date, never paraphrased.
