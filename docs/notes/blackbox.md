# BlackBox (alejandro-publius/blackbox-datahub) - reverse-engineering report (A2)

- Repo: https://github.com/alejandro-publius/blackbox-datahub, cloned to `references/blackbox-datahub/` (depth 50)
- Commit SHA: `b72a32c64e11404b1de98b517bde7e487ca35963`
- License: **Apache-2.0** ("Copyright 2026 BlackBox contributors", full text in `LICENSE`, no `NOTICE` file). Permits copy/modify with: keep license + copyright notices, state changes, include license text. Gate result: copying is *permitted*; we nevertheless wrote all AEO code from scratch (DataHub/DuckDB/SQL coupling makes literal reuse impractical) and credit the idea in `docs/notes/third-party-a2.md`. Lead must add the attribution line to `THIRD_PARTY.md`.
- Stack: Python 3.11 + FastAPI + sse-starlette backend (`backend/blackbox/`), Anthropic tool-use loop, JSON-file incident store, Next.js frontend, DuckDB warehouse, DataHub (OSS) for metadata. Single-incident-at-a-time demo architecture, ~2.9k LOC backend.

## What it does
`READ -> PROVE -> ACT -> VERIFY -> WRITE`: an LLM drives a tool loop over DataHub lineage/metadata + warehouse profiling; deterministic tools record `EvidenceItem` facts; `confirm_root_cause` is machine-gated; a repair is applied, the pipeline rebuilt and a 32-invariant pytest suite plus a KPI-ratio band decide `VERIFIED`; only then a git branch, optional GitHub PR, and DataHub writeback happen.

## Component findings

### Incident state machine (`backend/blackbox/models.py`, `store.py`, `agent/tools.py`)
- `IncidentStage` enum + `STAGE_ORDER` list: REPORTED -> CONTEXT_DISCOVERY -> LINEAGE_TRAVERSAL -> HYPOTHESIS_GENERATION -> EVIDENCE_COLLECTION -> ROOT_CAUSE_CONFIRMED -> REPAIR_GENERATED -> REPAIR_TESTING -> VERIFIED -> WRITEBACK_COMPLETE; NO_INCIDENT/FAILED are terminal exits.
- `IncidentState.can_advance_to(target)`: strictly *forward by index* (skips allowed, backward forbidden, terminals sticky); `IncidentStore.advance()` raises on illegal. No actor/reason, no audit record, no explicit edge map, gates are enforced in tool code not in the machine. Weak compared with what we need (explicit ALLOWED map, no skipping, audit).
- Tests: `tests/test_engine.py` `test_stage_order_forward_only`, `test_terminal_stages_are_sticky`.

### Evidence gates (`agent/tools.py::t_confirm_root_cause`, `t_declare_no_incident`, `t_update_hypothesis`, `_evidence_is_anomalous`)
- Facts (`EvidenceItem`: kind, source, transport, data) are created only by deterministic tool code; the LLM cites `evidence_ids`.
- `confirm_root_cause` rejects unless: all cited ids exist; a quantitative (profile/baseline) item is cited; a DataHub-sourced item is cited; blamed asset is in traversed lineage; quantitative evidence actually mentions the blamed field and asset; and at least one cited quantitative item shows a real anomaly (ratio-like field outside 1/1.5..1.5) - i.e. *contradictory evidence cannot confirm*. Errors are returned as strings listing every problem.
- `declare_no_incident` mirror: needs quantitative evidence and none of it may be anomalous.
- Hypothesis confirm/eliminate requires valid evidence ids. Repair target must match the diagnosed asset (`_repair_target_mismatch`).
- Best idea for us: "every requirement is checked and all failures listed at once; citing evidence is not enough, the cited numbers must agree with the claim."

### Approval gates
- Two-phase run: `run_investigation(pause_before_repair=True)` stops after ROOT_CAUSE_CONFIRMED; `POST /api/incidents/{id}/repair` (only valid in that stage) authorises phase 2 (`run_repair_phase`). The tool layer refuses `propose_repair` unless `allow_repair`. That is an operator checkpoint, not a persisted Approval entity (no approver, note, modify, reject). PR creation is separately opt-in via `BLACKBOX_CREATE_PR`.

### Verification (`repair.py::verify_repair`, `warehouse.py`, `pipeline/invariants/test_invariants.py`)
- Rebuild warehouse with patched transform, run the full invariant pytest suite, recompute KPI; success = 0 failures AND 0.8 <= anomaly_ratio <= 1.3. Failure keeps the incident un-VERIFIED and returns failures to the model; unverified patches are reverted (`_cleanup_unverified_patch`). Eval `bad_repair_rejected` proves a naive patch is rejected. DataHub/DuckDB-specific, but the principle (post-change measured check gates VERIFIED; failing check reverts) maps to our delayed Profound observation verification.

### SSE (`api.py::incident_events`, `store.py`)
- Full-state *snapshot* per event over `sse-starlette`, in-process `asyncio.Queue` per subscriber (maxsize 256, drop on full, 15 s ping), `loop.call_soon_threadsafe` from worker threads. Idempotent/reconnect-safe by design. We use persisted `IncidentEvent` rows + EventBus (Redis fan-out), so only the snapshot-on-connect + ping idea is relevant; their snapshot model is not our event-log model.

### GitHub PR execution (`repair.py::make_git_artifact`, `try_create_pr`, `publish_repair_pr`, `build_pr_body`)
- Commit on `blackbox/fix-<id>` via temporary git worktree; push **only** refs prefixed `blackbox/fix-`; `gh pr create --body-file`; reuses an existing PR; never raises, returns `{status: disabled|skipped|created|failed, url, detail}`; disabled by default; runs strictly after verification; PR body rendered **only from recorded evidence** (no model prose), with disclosure of autonomous authorship. Shells out to `gh` + `git` (not API). Directly relevant to A10 as a design reference: branch-prefix allow-list, dry-run default, never-raise result dict, body from recorded state only. Code itself is tied to DataHub IncidentState and subprocess `gh`; A10 should use REST/PyGithub with approval check.

### Incident history / persistence (`store.py`)
- One JSON file per incident, atomic tmp+rename, in-memory cache, `latest()`. No audit trail beyond the evidence list. We have Postgres + AuditEvent/IncidentEvent, so nothing to reuse.

### Tests / evals (`tests/`, `evals/`)
- 5 test modules (state machine, gate rejections incl. contradictory evidence, repair PR, tracing, context-kit fallbacks), monkeypatched subprocess for PR tests (assert push refspec, no network). Evals: 5 scenarios with deterministic graders computed from final state + git/pytest (never LLM self-report), incl. false-positive control (`control_no_incident`), distractor rejection, ablation. Idea reusable for A13: grade from persisted state, include a "no incident / no fabrication" control, a negative control for the gate. Fixtures are DataHub-specific.

### Safety/honesty patterns worth copying as ideas
1. LLM proposes, deterministic code establishes facts; facts carry transport/provenance.
2. Gate failures are descriptive lists, not booleans.
3. Contradictory evidence invalidates the claim it is cited for.
4. Mutations only after verification; isolated, never-raising publish step.
5. Control scenario that must yield "no incident".

## Machine-readable reuse report

Format: `component | decision | source path(s) (relative to references/blackbox-datahub) | AEO target | rationale`

<!-- REUSE_TABLE_START -->
| component | decision | source_paths | aeo_target | rationale |
|---|---|---|---|---|
| Stage enum + forward-only ordering | REIMPLEMENT | backend/blackbox/models.py (IncidentStage, STAGE_ORDER, can_advance_to) | backend/app/incidents/state_machine.py | Idea kept (forward-only, terminals sticky); AEO needs explicit ALLOWED edge map over IncidentState, actor/reason, audit record, dismissed/failed from anywhere, no skipping verification. |
| Guarded advance on store | REIMPLEMENT | backend/blackbox/store.py (advance) | backend/app/incidents/state_machine.py (transition) | Raises on illegal like theirs; ours returns audit dict. JSON-file store irrelevant (Postgres). |
| Root-cause evidence gate | REIMPLEMENT | backend/blackbox/agent/tools.py (t_confirm_root_cause) | backend/app/investigation/evidence_gate.py | Same principle (all failures listed, cited evidence must agree). Requirements, types and thresholds are AEO-specific (Profound, content, citations, timestamps, policy dataclass). Their checks are DataHub/field-name/SQL specific. |
| Contradiction check (ratio outside band) | REIMPLEMENT | backend/blackbox/agent/tools.py (_evidence_is_anomalous, ANOMALY_RATIO_THRESHOLD) | backend/app/investigation/evidence_gate.py (_metric_change, contradiction scoring) | Idea: cited evidence that disagrees cannot confirm. AEO uses support/contradiction scores from EvidenceRanker, not warehouse ratios. |
| Evidence item model with transport/provenance | ADAPT (concept) | backend/blackbox/models.py (EvidenceItem) | backend/app/models/evidence.py (A6) | AEO Evidence already richer (hashes, retrieval_method, scores). Recommend A6 keep a transport/retrieval_method field; nothing copied. |
| Hypothesis lifecycle requires evidence ids | ADAPT (concept) | backend/blackbox/agent/tools.py (t_update_hypothesis) | investigation/rca.py (A3), evidence_gate.py | Gate rejects hypotheses citing no/unknown evidence ids. |
| declare_no_incident / observe path | ADAPT (concept) | backend/blackbox/agent/tools.py (t_declare_no_incident) | state_machine (observe-only edge ROOT_CAUSE_PROPOSED->INTERVENTION_PROPOSED) | Maps to AEO `observe` action for low-confidence root cause. |
| Two-phase operator approval checkpoint | ADAPT (concept) | backend/blackbox/api.py (start_repair), agent/investigator.py (pause_before_repair) | app/services/approvals.py (A5), api (A12) | AEO needs a persisted Approval (approver, note, modify/reject); the pause-before-mutation idea is the same. |
| Verification gate (rebuild + invariants + KPI band, revert on failure) | ADAPT (concept) | backend/blackbox/repair.py (verify_repair), agent/investigator.py (_cleanup_unverified_patch) | app/experiments, app/learning verification (A5/A9) | AEO verification = delayed Profound observation window, not a local pytest suite. State machine enforces AWAITING_VERIFICATION->VERIFIED, no skip. |
| GitHub PR branch allow-list + never-raise result dict + disabled default | ADAPT (concept) | backend/blackbox/repair.py (try_create_pr, publish_repair_pr, FIX_BRANCH_PREFIX) | backend/app/connectors/github, interventions (A10) | Reuse the design (prefix allow-list, dry-run default, status dict, run only after approval). Code shells out to git/gh; A10 should use REST API. If A10 copies code, Apache-2.0 attribution + "modified" notice required. |
| PR body rendered only from recorded evidence | ADAPT (concept) | backend/blackbox/repair.py (build_pr_body, _cited_evidence, _quantitative_block) | interventions (A10) | Same principle: no model prose for facts; content differs entirely. |
| git worktree commit of fix | IGNORE | backend/blackbox/repair.py (make_git_artifact) | - | Local-repo mutation approach; AEO opens PRs on a customer repo via API. |
| SSE snapshot stream | ADAPT (concept) | backend/blackbox/api.py (incident_events), store.py (subscribe/_publish) | backend/app/core/events.py, api (A12) | Keep "send current state on connect + 15s ping + drop on slow consumer". AEO streams persisted IncidentEvent rows with Redis pub/sub. |
| JSON-file incident store / history | IGNORE | backend/blackbox/store.py | - | Postgres models + AuditEvent supersede. |
| Anthropic tool-use investigator loop | IGNORE | backend/blackbox/agent/investigator.py, prompts.py, tools.py TOOL_SCHEMAS | - | AEO RCA is rules-first with optional LLM structured output (A3); different tool set. |
| DataHub client / MCP bridge / writeback / context kit | IGNORE | backend/blackbox/datahub/** | - | DataHub specific. |
| DuckDB warehouse, SQL tools, invariants, pipeline | IGNORE | backend/blackbox/warehouse.py, pipeline/**, tools.py t_run_sql | - | SQL/DuckDB specific. |
| Tracing (OpenTelemetry) | IGNORE | backend/blackbox/tracing.py | - | Out of scope; structlog used. |
| Frontend (Next.js command center) | IGNORE | frontend/** | - | Nuxt/UI.md contract governs. |
| Test pattern: gate rejection matrix incl. contradictory evidence | ADAPT (concept) | tests/test_engine.py | backend/tests/unit/test_evidence_gate.py | Same style: one test per rejection reason + one acceptance test. Written fresh. |
| Test pattern: monkeypatched subprocess PR safety tests | ADAPT (concept) | tests/test_repair_pr.py | A13 / A10 tests | Assert only allow-listed refspec pushed, disabled flag does no work, errors swallowed. |
| Eval pattern: deterministic graders + false-positive control + ablation | ADAPT (concept) | evals/scenarios.py, evals/harness.py | A13 replay/integration tests | Grade from persisted state, never LLM self-report; include a "no incident" control. |
<!-- REUSE_TABLE_END -->

No row is `REUSE_DIRECTLY`: nothing was copy-compatible with AEO without stripping DataHub/DuckDB specifics, and the generic parts (ordered stage list, evidence-id checks) are tiny and were rewritten from scratch.
