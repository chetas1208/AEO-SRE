# Architecture

Modular Python monolith + async workers. Postgres (pgvector image) and Redis carry everything; there is no other
service. Frontend is a separate Nuxt app that talks only to the REST/SSE API.

```
 Profound API ──► ingestion ──► Signal rows ─┐                        ┌─► intervention package (manual, default)
 public web  ──► web collector ► Evidence ───┤                        │
                                             ▼                        │
   detector (rolling baseline, no LLM) ► Incident ► investigation ► evidence graph ► hypotheses (rules, optional LLM)
        │                                                    │
        │                         evidence gate (deterministic) ──► root cause CONFIRMED | stays PROPOSED
        ▼                                                    │
   priority breakdown          policy (LinUCB + cold-start priors, versioned) ► candidate interventions
                                                             │
                              Experiment ledger row (status proposed) + Approval(pending)
                                                             ▼
                             HUMAN approve / reject / modify  ──►  experiment ACTIVATED ► executor (Manual by default)
                                                             ▼
             Manual: package issued (awaiting_human_execution) ► human records executed (POST /executed)
                      Experiment executed ► verification window ► Observation (Profound signals after window)
                                                             ▼
                                   Reward (measured, components stored) ► new immutable PolicyVersion
```

## Layout

| Path | Role |
|---|---|
| `backend/app/api/` | FastAPI routes, mappers, error model; OpenAPI at `/openapi.json`; SSE at `/api/incidents/{id}/events` |
| `backend/app/models/` | SQLAlchemy 2 models: `core` (org, signal, incident, job, audit, event, setting), `evidence`, `interventions` (intervention, approval, execution, experiment, observation, reward), `policy` |
| `backend/app/services/pipeline.py` | The orchestrator: one coroutine per job stage; the only place stages are chained |
| `backend/app/workers/` | arq `WorkerSettings`, job handlers (`jobs.py`), enqueue helper (`queue.py`), cron fan-out |
| `backend/app/incidents/` | `detector` (thresholds + rolling baseline), `priority` (7-component geometric score), `state_machine` |
| `backend/app/investigation/` | `collector` (web evidence), `rca` (rule hypotheses + optional LLM), `evidence_gate` |
| `backend/app/evidence/` | graph (NetworkX over persisted rows), provenance helpers, `ranker` (EvidenceRanker runtime) |
| `backend/ml/` | EvidenceRanker training/eval (FEVER + VitaminC subsets), artifact in `ml/artifacts/evidence_ranker/` |
| `backend/app/policy/`, `learning/` | LinUCB/Thompson, features (context vector), cold-start priors, versioned store; reward, ingestion, OPE |
| `backend/app/interventions/` | candidate generation per `ActionType`, patch templates, `InterventionExecutor` interface, `ManualExecutor` + `record_manual_execution` (default), optional `GitHubPRExecutor`, `ProfoundAgentExecutor` stub |
| `backend/app/experiments/` | ledger, status machine, verification helpers, outcome ranges |
| `backend/app/connectors/` | `profound`, `web`, `llm` (provider-neutral `ModelGateway`; fast tier `claude-haiku-4-5`, deep tier `claude-sonnet-4-6`, escalation by complexity score or validation failure), `github` (optional executor only) |
| `backend/app/changeguard/` | Change Guard feature (see below); routes `change_checks`, `canonical_claims` |
| `backend/migrations/` | Alembic: `0001` initial schema, `0002` domain integrity constraints, `0003` `signals.source` 128, `0004` Change Guard (head = `0004`) |
| `frontend/` | Nuxt 4 + TypeScript: `/incidents`, `/incidents/[id]`, `/experiments`, `/settings` |

## Jobs and the pipeline

Every job is a row in `jobs` (`queued -> running -> success|failed`, attempts, error, result) and an arq task. Handlers
call `services/pipeline.py`:

| Job kind | Stage | Next |
|---|---|---|
| `ingest_profound_signals` | `pipeline.ingest` (Profound unavailable => recorded no-op) | `detect_incidents` |
| `detect_incidents` | `pipeline.detect` | `investigate_incident` for incidents above the action threshold |
| `investigate_incident` | profound evidence -> web evidence -> rank -> hypotheses -> graph -> gate | `score_interventions` |
| `fetch_external_evidence`, `rank_evidence`, `generate_hypotheses` | single stages, re-runnable | |
| `score_interventions` | policy decision, candidates, experiment (proposed), approval (pending) | waits for a human |
| `execute_intervention` | re-check approval, executor, ledger | verification waits for the window |
| `verify_experiment` | record Observation from post-window signals | `calculate_reward` |
| `calculate_reward` | `learning.ingest_reward`: Reward row + new PolicyVersion, atomic and idempotent | |
| `update_policy` | confirms (or performs) the policy update for an experiment | |

Cron: ingest at :00/:30, detect at :05/:35, verify-due every 15 min; one Job per organisation so failures are isolated.
Retries: 3 tries, backoff 15/60/240 s for unexpected errors; `PermanentError` and approval errors never retry. If the
queue is unreachable the pipeline runs the next stage inline and emits a WARNING event.

Failure containment (Plan section 7): each investigation step is wrapped; a failed step becomes a FAILED SSE event,
`incident.context.investigation.failed_steps`, and `investigation_status = incomplete`; later steps still run.

## Incident state machine

`detected -> triaged -> investigating -> evidence_ready -> root_cause_proposed -> root_cause_confirmed ->
intervention_proposed -> awaiting_approval -> approved -> executing -> executed -> awaiting_verification -> verified ->
rewarded -> closed` (+ `dismissed`, `failed`). The only shortcut is `root_cause_proposed -> intervention_proposed`, legal
only for the `observe` action. Every transition is audited and emitted. Verification cannot be skipped.

## Safety invariants (enforced in code, covered by tests)

1. A root cause is `proposed` until `evidence_gate.confirm_aeo_root_cause` passes every requirement.
2. If the gate does not confirm, the policy decision is a `rule_fallback` to `observe` (probability 1.0, excluded from OPE).
3. No external mutation without an approval decided by a human actor; executors re-check. The default executor (manual) mutates nothing; only a human can record a manual execution (`dry_run=false`, 409 on repeat, never future-dated).
4. A GitHub dry-run preview can never be verified or rewarded; manual executions are real and are.
5. Reward only from measured post-window Signals; no observation => stays `awaiting_verification`.
6. Policy versions are immutable; every update creates a new row with `parent_id` and `source_experiment_id`.
7. No demo mode: no hardcoded incidents or metrics in app code (`tests/unit/test_no_demo_mode.py`).

## Events and audit

`app.core.events.emit` persists an `IncidentEvent` (replayable by `seq`) and publishes on Redis pub/sub
(in-process fallback). The SSE endpoint replays history then streams live. `app.core.audit.audit` writes append-only
`audit_events` for every state transition, approval, execution and policy update.

## Change Guard

Feature inside the monolith (DEC-041; spec `docs/CHANGE_GUARD_SPEC.md`). Package `backend/app/changeguard/`.

```
 Profound Agent (Profound's cloud) ──Call API node──► POST /api/change-checks   (Bearer CHANGE_GUARD_TOKEN)
                                                         │ ChangeSet (source_mode LIVE | SIMULATED)
                                                         ▼
                                                  Profound Lift Change Guard
              1 experiment contamination ── experiments/collision + window.py ──► DELAY (+ eligible_after)
              2 duplicate / conflicting target changes ────────────────────────► MERGE | REQUIRE_REVIEW
              3 canonical-truth conflict: rules, then EvidenceRanker / ModelGateway if READY ► BLOCK
                                                         ▼
                              one decision (BLOCK > DELAY > REQUIRE_REVIEW > MERGE > ALLOW) + all findings
                                                         ▼
                                    the Agent branches on `decision`
```

Invariants: checks never mutate experiments; degraded semantics are reported, not hidden; empty canonical truth is reported as
skipped; every decision stores a digest and approvals bind to it (`409 APPROVAL_DIGEST_MISMATCH` at activation/execution). AEO
SRE's own interventions run the same guard through an internal service call, never through the token. Reachability from
Profound's cloud is an external requirement that is not solved (see `docs/CHANGE_GUARD_INTEGRATION.md`).

## Planned: Neo4j projection (not built)

A Neo4j graph of events, agent runs, ChangeSets, conflicts, decisions, experiments and outcomes is specified in `docs/GRAPH_SPEC.md` (DEC-043). Postgres stays the system of record; Neo4j would be a rebuildable projection fed by a Postgres outbox, never on the transactional path, and the policy falls back to the deterministic baseline when the graph is unavailable. Infrastructure is in progress: at the end of this docs pass an uncommitted `backend/app/graph/` package (client, capabilities, errors, health) and an additive `neo4j_state` field on `/api/health` were appearing in the working tree, but no projector exists and nothing is projected. `/api/health` served by the running (older) process reports `database`, `redis`, `profound`, `model_provider`, `profound_state`, `model_state` and `git_sha`.
