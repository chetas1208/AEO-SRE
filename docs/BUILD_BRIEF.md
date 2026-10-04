# Profound Lift — Build Brief (shared by all build agents)

> Historical working record. Agent labels in the ownership table (e.g. "A2 BlackBox") are internal work-package names from the build campaign, not statements about code origin. See THIRD_PARTY.md for what is actually incorporated.

Workspace: `/home/923873155/Marketing Hackathon 3Oct` (monorepo). Read `Plan.md` (product) and `UI.md` (UI contract; frontend agents read fully) first. This brief is execution contract: file ownership, shared vocabulary, conventions.

## Hard rules
1. No fake data: no DEMO_MODE, no hardcoded incidents/metrics in app code. Fixtures only under `backend/tests/` or `backend/tests/fixtures/`, clearly labeled.
2. Never fabricate Profound data, citations, rewards, outcomes. Unavailable → expose `unavailable`/`degraded`.
3. Root cause stays `proposed` until the evidence gate confirms it. Reward never set without measured post-intervention observation.
4. `observe` is a valid action. Action vocabulary is fixed (`ActionType`).
5. Human approval before any external mutation. Never auto-merge. (Superseded by A16) Execution is an interface; the default is the ManualExecutor (no credentials): it issues an intervention package and a human records the execution. GitHub is an optional executor only when explicitly selected AND configured.
6. License gate: before copying ANY code from `references/*`, read that repo's LICENSE at the cloned SHA. No license / ambiguous / restrictive → DO NOT COPY, reimplement from the idea. Record every reference in `docs/notes/third-party-<agent>.md` (repo, SHA, license, code reused y/n, files/concepts adapted, attribution). Lead merges into `THIRD_PARTY.md`.
7. No secrets in git. Single root `.env` (already exists, copied from `.env.example`; keys may be empty). Do not create other env files.

## Layout & ownership (edit ONLY files you own; read anything)
Python package root: `backend/app` (import as `app.*`). Python venv: `backend/.venv` (uv; run `cd backend && .venv/bin/python -m pytest`). Add deps with `cd backend && uv pip install <pkg>` AND append to `backend/pyproject.toml` (coordinate: only append lines, never rewrite).

| Agent | Owns |
|---|---|
| A1 Auditor | `docs/notes/audit.md` |
| A2 BlackBox | `backend/app/incidents/state_machine.py`, `backend/app/investigation/evidence_gate.py`, `references/blackbox-datahub/`, `docs/notes/blackbox.md` |
| A3 CampaignPilot | `backend/app/incidents/detector.py`, `.../priority.py`, `backend/app/investigation/rca.py`, `references/campaignpilot/`, `docs/notes/campaignpilot.md` |
| A4 Info-Ninja | `backend/app/connectors/web/**`, `backend/app/investigation/collector.py`, `references/info-ninja/` |
| A5 LaunchPilot | `backend/app/models/interventions.py`, `backend/app/experiments/**`, `backend/app/services/approvals.py`, `references/launchpilot/` |
| A6 Evidence DAG | `backend/app/models/evidence.py`, `backend/app/evidence/graph.py`, `.../provenance.py`, `backend/app/schemas/evidence.py`, `references/lattice/` |
| A7 Profound | `backend/app/connectors/profound/**`, `backend/app/services/ingestion.py` |
| A8 ML | `backend/ml/**`, `backend/app/evidence/ranker.py` |
| A9 Bandit | `backend/app/policy/**`, `backend/app/learning/**`, `backend/app/models/policy.py` |
| A10 Executor | `backend/app/interventions/**`, `backend/app/connectors/github/**`, `references/slap/`, `references/recoil-ai/` |
| A11 Frontend | `frontend/**` |
| A12 API/Domain | `backend/app/api/**`, `backend/app/schemas/*` (except evidence.py), `backend/app/models/core.py`, `backend/app/core/events.py`, `backend/app/core/audit.py`, `backend/app/services/incidents.py`, `backend/app/services/capabilities.py` |
| A13 Tests | `backend/tests/**`, `frontend` test files (`frontend/tests/**`) |
| A14 Integration lead | `backend/migrations/**`, `backend/alembic.ini`, `backend/app/workers/**`, `backend/app/services/pipeline.py`, root files (README, THIRD_PARTY.md, Makefile, docker-compose.yml, docs/ARCHITECTURE.md), **all git commits**; may edit any file to reconcile |
| Already written (lead, may be edited by A14 only) | `backend/app/core/config.py`, `backend/app/core/db.py`, `backend/app/domain/enums.py`, `backend/app/models/__init__.py`, `backend/pyproject.toml` |

If you need a change in a file you don't own: write a short request in `docs/notes/requests-<agentN>.md` (what, why, exact signature) and continue with a local adapter/stub. A14 and owners resolve it. Do NOT run `git commit/add/reset` (A14 only; concurrent git corrupts the index). You may `git clone` into `references/` (gitignored) — use `--depth 50`.

## Shared vocabulary
`backend/app/domain/enums.py` is the single source: `Severity, IncidentState, IncidentCategory, ActionType, ApprovalStatus, ExperimentStatus, EvidenceType, EvidenceStatus, EdgeType, HypothesisStatus, StepStatus, SelectionBasis, Risk, CapabilityState`. Import, never redefine. Need a new value → request.

## Models contract (SQLAlchemy 2 typed `Mapped[...]`, `app.core.db.Base`, `TimestampMixin`, PK `id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)`; JSON columns use `sqlalchemy.JSON` (JSONB variant allowed via `.with_variant`))
- `app/models/core.py` (A12): `Organization(name, domain, competitor_domains JSON, canonical_domains JSON, personas JSON, topics JSON)`, `Signal(org_id, kind, source, metric, value, baseline, observed_at, prompt_cluster_id?, raw JSON, raw_payload_ref)`, `Incident(number:int unique autoinc-style, org_id, title, category, severity, priority:float, priority_breakdown JSON, state, detected_at, first_observed_at, prompt_cluster_id?, metrics JSON(list of MetricDelta), confidence, summary, investigation_status, context JSON)`, `PromptCluster(org_id, topic, prompts JSON)`, `Job(kind, status, payload JSON, result JSON, attempts, error)`, `AuditEvent(actor_type[system|human|model], actor, entity_type, entity_id, event, metadata JSON, at)`. Also `IncidentEvent(incident_id, stage, status:StepStatus, message, metadata JSON, at)` = SSE log persisted.
- `app/models/evidence.py` (A6): `Evidence(incident_id, type:EvidenceType, status:EvidenceStatus, title, source, url, content_hash, excerpt, retrieval_method, retrieved_at, observed_at, support_score, contradiction_score, insufficient_score, freshness_risk, confidence, raw JSON)`, `EvidenceEdge(incident_id, src_id, dst_id, edge_type:EdgeType, confidence, provenance JSON)` (nodes may be evidence rows or synthetic node rows: add `EvidenceNode` if needed), `Hypothesis(incident_id, title, summary, status:HypothesisStatus, confidence, evidence_ids JSON, rationale, produced_by)`.
- `app/models/interventions.py` (A5): `Intervention(incident_id, hypothesis_id?, action:ActionType, title, rationale, risk, proposed_change JSON(files/diff/target), score, selected:bool, selection_basis, policy_version_id?)`, `Approval(intervention_id, status, decided_by, decided_at, note, modified_change JSON)`, `Execution(intervention_id, executor, status, reference(PR url), dry_run:bool, log JSON, started_at, finished_at, error)`, `Experiment(number, incident_id, intervention_id, policy_version_id, policy_probability, context_vector JSON, alternatives JSON, evidence_snapshot JSON, before_metrics JSON, after_metrics JSON, status:ExperimentStatus, verification_window_start/end, executed_at, evaluated_at)`, `Observation(experiment_id, metrics JSON, observed_at, source)`, `Reward(experiment_id, components JSON, total, weights JSON, computed_at)`.
- `app/models/policy.py` (A9): `PolicyVersion(version:str "v0.0.1", algorithm, state JSON(A,b matrices serialized), priors JSON, n_updates, parent_id, created_at, immutable)`, `PolicyDecision(incident_id, policy_version_id, context_vector JSON, scores JSON, selected_action, probability, selection_basis, cold_start:bool)`.
All tables must be creatable via `Base.metadata.create_all` for tests. A14 generates the Alembic migration from metadata.

## Cross-module Python interfaces (stable import paths; owners implement, others stub until ready)
- `app.incidents.state_machine`: `can_transition(src, dst) -> bool`, `transition(incident, dst, actor, reason)` (raises `IllegalTransition`), `ALLOWED: dict[IncidentState,set[IncidentState]]`.
- `app.investigation.evidence_gate`: `confirm_aeo_root_cause(hypothesis, evidence: list, policy: EvidencePolicy|None=None) -> GateResult(confirmed:bool, reasons:list[str], missing:list[str])`.
- `app.incidents.detector`: `detect_incidents(session, org_id) -> list[Incident]` (rolling baseline over Signals; no LLM). `app.incidents.priority`: `compute_priority(...) -> PriorityResult(score 0-100, breakdown dict)` and `severity_from_priority`.
- `app.investigation.rca`: `generate_hypotheses(incident, evidence, llm=None) -> list[HypothesisDraft]` (deterministic rules first, optional LLM structured output; LLM absent → rules only).
- `app.connectors.web`: `WebCollector.fetch(url) -> FetchResult(status, content_hash, text, blocks, fetched_at, error)`; `app.investigation.collector.collect_evidence(session, incident, emit) -> list[Evidence]`.
- `app.connectors.profound`: `ProfoundClient`, `ProfoundClient.capabilities() -> dict[str,CapabilityState]`; `app.services.ingestion.ingest_org(session, org_id)`.
- `app.evidence.ranker`: `EvidenceRanker.load()`, `.score(claim:str, passage:str, meta:dict) -> RankerScore{support,contradiction,insufficient,freshness_risk}`, `.available:bool`; heuristic fallback when no artifact.
- `app.policy`: `Policy.score(context:dict) -> list[ActionScore]`, `Policy.select(context) -> Decision`, `Policy.update(decision_ctx, action, reward)`, `PolicyStore` (persist/load versions), `encode_context(incident, ...) -> np.ndarray`, `COLD_START_PRIORS`.
- `app.learning`: `compute_reward(before, after, action, risk, weights) -> RewardResult(components, total)`, `ingest_reward(session, experiment_id)` → new PolicyVersion.
- `app.interventions`: `propose_interventions(session, incident, decision) -> list[Intervention]`, `Executor` protocol, `GitHubPRExecutor(dry_run=...)`, `build_diff(...)`.
- `app.core.events`: `EventBus.emit(incident_id, stage, status, message, metadata)` persists `IncidentEvent` + fans out to SSE subscribers (Redis pub/sub; in-process fallback). Everything that "does work" calls `emit(...)`.
- `app.core.audit`: `audit(session, actor_type, actor, entity_type, entity_id, event, metadata)`.

## API contract (A12 implements; A11 consumes; JSON snake_case on wire; frontend maps)
`/api/health`, `/api/system/capabilities`, `/api/organizations` (GET/POST {domain,name?}), `/api/incidents` (filters severity,status,topic,range; returns `IncidentSummary` incl. counts), `/api/incidents/detect` (POST), `/api/incidents/{id}` (detail incl. metrics, priority_breakdown, state), `/api/incidents/{id}/investigate` (POST → enqueue job), `/api/incidents/{id}/evidence`, `/graph`, `/hypotheses`, `/prompts`, `/interventions`, `/events` (SSE), `/api/interventions/{id}/approve|reject|modify|execute`, `/api/experiments`, `/api/experiments/{id}`, `/api/experiments/{id}/verify`, `/api/policy`, `/api/policy/versions`, `/api/settings`. `{id}` accepts UUID. Response shapes mirror UI.md §51 interfaces plus extras; A12 publishes OpenAPI at `/openapi.json`; A11 generates TS types from it where practical (`openapi-typescript`), else hand-written mirror in `frontend/types/`.

## Conventions
Python 3.12, type hints, async SQLAlchemy sessions, pydantic v2 schemas, `structlog`, ruff-clean (line 110). Match surrounding style, minimal comments. Postgres at `localhost:5432` (user/pass/db `aeo`) and Redis at `localhost:6379` are running via docker compose; tests may use a separate DB `aeo_test` (A13 creates) or aiosqlite for pure-unit tests. Run your own tests before declaring done; leave a handoff note at `docs/notes/handoff-<agent>.md` (what exists, public interfaces, what's stubbed, known gaps, requests for others). Keep working through ordinary blockers; stop only for genuine unrecoverable issues.
