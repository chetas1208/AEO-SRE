# A12 handoff: API / domain

Run: `cd backend && .venv/bin/python -m app.core.init_db && .venv/bin/uvicorn app.api.main:app --port 8000`
(`/api/health`, `/openapi.json`, `/docs` verified live against Postgres + Redis). Tests: `.venv/bin/python -m pytest tests/api` (41 pass, Postgres and `AEO_TEST_DB=sqlite`).
Wire format is snake_case JSON. Errors are always `{"error": {"type", "message", "details", "request_id"}}`; `type` is one of
`not_found | validation_error | illegal_transition | conflict | forbidden | unavailable | not_implemented | internal_error`.
Status codes: 404 unknown id, 409 illegal state-machine/approval transition (or duplicate), 422 validation, 503 job queue down, 501 backing module missing.
Identity: `X-Actor` request header names the human actor (default `operator`); recorded in audit + approvals (`actor_type=human`). No auth yet.
Incident `{id}` accepts UUID, integer number (`1042`) or `INC-1042`. Experiment `{id}` accepts UUID, `42` or `EXP-0042`.
Pagination: `limit` (1-200, default 50) and `offset`; page bodies carry `total`.

## Files
`app/models/core.py` (Organization, PromptCluster, Signal, Incident, IncidentEvent, Job, AuditEvent, Setting), `app/core/{events,audit,queue,init_db}.py`,
`app/api/{main,deps,errors,logging,mappers,adapters}.py`, `app/api/routes/*.py`, `app/schemas/{common,system,organizations,incidents,interventions,experiments,policy,settings}.py`,
`app/services/{incidents,capabilities}.py`, `tests/api/test_smoke.py`.

## Route table
| Method + path | Request | Response |
|---|---|---|
| GET `/api/health` | | `{status, version, time, database, redis}` (503 if DB down) |
| GET `/api/system/capabilities?probe=` | `probe=true` also tests LLM/GitHub endpoint reachability | `{checked_at, overall, capabilities{api,database,redis,workers,profound,crawler,ml_ranker,policy,llm,github: {key,label,state,detail,last_success,last_error,last_error_at,meta}}, last_ingestion, last_investigation, last_policy_update, model_artifact_version}`. Profound `meta.surfaces` = `capability_report()` per surface (state, verification, reason, endpoint); `meta.last_sync` = `Setting profound.last_sync.<org_id>`. LLM comes from `app.connectors.llm.llm_capability()` (per-process call tracking). Workers = arq heartbeat key. |
| GET `/api/organizations` | | `OrganizationOut[]` (+ incident_count, signal_count, last_signal_at) |
| POST `/api/organizations` | `{domain, name?}` | 201 `OrganizationOut + ingest_job{job_id,kind,status,error}`; enqueues `ingest_profound_signals`. 409 duplicate domain, 422 bad domain. Queue down: org still created, `ingest_job.status="unavailable"`. |
| GET/PATCH `/api/organizations/{id}` | PATCH `{name?, competitor_domains?, canonical_domains?, personas?, topics?}` | `OrganizationOut` |
| GET `/api/incidents` | `org_id, severity, status, topic, range(1h\|24h\|7d\|14d\|30d\|90d), q, sort(priority\|detected), limit, offset`; `severity`/`status` repeatable or comma separated; `status` accepts UI status or raw state | `{items: IncidentSummary[], total, limit, offset, counts_by_severity{all,critical,high,medium,low}, counts_by_status{all,<ui status>...}, counts_by_state, topics[]}`. Counts ignore their own filter so tabs stay populated. |
| POST `/api/incidents/detect` | `{org_id?}` (all orgs if omitted) | 202 `{jobs: JobRef[]}` (job `detect_incidents`) |
| GET `/api/incidents/{id}` | | `IncidentDetail` = summary + `summary, confidence, metrics[<=4 MetricDelta], priority_breakdown{score,components[{key,label,value 0-100,weight,source,note}],method,raw}, primary_cta{key,label,enabled,reason,intervention_id,target_tab}, allowed_actions[{action,enabled,reason}], allowed_next_states, affected_prompt_count, active_job, expected_outcome{available,n,low,high,metric,reason}, experiment_id, context` |
| POST `/api/incidents/{id}/investigate` | | 202 `{incident_id, job, state}` (job `investigate_incident`). 409 if running / state not investigable. A stalled `investigating` incident (no active job) may be retried. |
| POST `/api/incidents/{id}/resolve` / `/dismiss` | `{reason?}` | `{incident_id, from_state, to_state}`; state machine enforced (409). Resolve = `closed`, only from verified/rewarded. |
| GET `/api/incidents/{id}/evidence` | `type?, status?` | `{incident_id, items: EvidenceItem[] (A6 schema), total, counts_by_type, counts_by_status}` |
| GET `/api/incidents/{id}/evidence/{evidence_id}` | | `EvidenceDetail` (excerpt, raw, provenance, edges) |
| GET `/api/incidents/{id}/graph` | | `GraphOut` (A6) |
| GET `/api/incidents/{id}/hypotheses` | | `HypothesisOut[]`, confidence desc, status stays `proposed` until the gate confirms |
| GET `/api/incidents/{id}/prompts` | `limit, offset` | `{incident_id, cluster_id, topic, items: PromptRow[], total, unavailable_reason}` from `PromptCluster.prompts` (strings or dicts) |
| GET `/api/incidents/{id}/interventions` | | `{items: InterventionOut[] (selected first, then score), selection_basis, policy_version, cold_start}` |
| GET `/api/incidents/{id}/events` | `Last-Event-ID` header or `?after_seq=` | SSE. `id:` = event seq, `data:` = `{id, seq, incident_id, timestamp, stage, status, message, metadata}`; replays persisted events after the cursor then streams live; 15s ping. |
| GET `/api/incidents/{id}/events/history` | `after_seq, limit` | same events as JSON array |
| GET `/api/events` | | Global SSE: named event `heartbeat` every 10s (Live pill) and `incident_event` for every persisted incident event (detected, state changes). No replay. |
| GET `/api/interventions/{id}` | | `InterventionOut` |
| POST `/api/interventions/{id}/approve` | `{note?}` | `{intervention, incident_state, experiment_id, job, message}` |
| POST `/api/interventions/{id}/reject` | `{note?}` | same; incident returns to `intervention_proposed` |
| POST `/api/interventions/{id}/modify` | `{modified_change, note?}` | same; a human edit that authorizes the edited change (`approval.status=modified`, counts as granted, incident `approved`) |
| POST `/api/interventions/{id}/execute` | `{dry_run?}` | 202 same shape with `job` (`execute_intervention`). 409 unless incident is `approved` and a human-granted approval exists. |
| GET `/api/experiments` | `status` (raw or `running\|awaiting_measurement\|verified`), `incident_id, action, limit, offset` | `{items: ExperimentRow[], total, summary{running,awaiting_measurement,verified,total}}`; row has `code, incident_title, action, started_at, display_status, before, after, reward, policy_version`. `after`/`reward` are null until measured. |
| GET `/api/experiments/{id}` | | `ExperimentDetail` sections per UI.md 30: `summary, why_selected, context_at_decision, evidence_snapshot, action_executed, approval, before_metrics, after_metrics, reward, policy, timeline, awaiting_reward` |
| POST `/api/experiments/{id}/verify` | `?force=` | 202 `{experiment_id, job}` (job `verify_experiment`); 409 unless executed/awaiting_verification and not dry-run. Reward only if a measured observation exists (A5/A14 logic). |
| GET `/api/policy`, PATCH | PATCH `{allowed_actions{action:bool}, min_confidence}` | `{current_version, algorithm, learning_mode(cold_start\|learning), cold_start, n_updates, allowed_actions, human_approval_required=true, min_confidence, last_update, model_artifact_version}`. `observe` can never be disabled. Persisted in `Setting("policy")` (A9 reads it). |
| GET `/api/policy/versions` | | `{items: PolicyVersionOut[], total}` |
| GET `/api/settings`, PATCH | PATCH `{org_id, organization{...}, policy{...}}` | `{organization, organizations, integrations[{key: profound\|github\|llm\|cms, connected, state, configured_fields (env var NAMES only), missing_fields, last_success, last_error, detail}], policy, environment}` Never returns secret values. |

## Behaviour notes
- `IncidentSummary` carries `state` (raw 17-value enum), `status` (UI lifecycle key) and `display_state` (label). Mapping per `docs/notes/audit.md` #6:
  detected/triaged -> detected; investigating/evidence_ready -> investigating; root_cause_proposed/confirmed -> needs_review; intervention_proposed/awaiting_approval/approved -> ready_for_action; executing/executed -> executing; awaiting_verification -> awaiting_measurement; verified/rewarded -> verified; closed -> resolved; dismissed, failed 1:1.
- Primary CTA (never more than one) is computed server-side from state: Investigate / Retry investigation / Investigation running (disabled) / Review evidence / Approve & Execute / Execute approved change / Executing / Awaiting post-intervention observation / Resolve.
- Approve flow: `appr.request_approval` (idempotent) -> incident `intervention_proposed -> awaiting_approval` if needed -> `decide_approval(actor_type="human")` -> incident `approved` -> `ledger.attach_approval` on the open experiment (if proposed) -> audit + SSE `approval_*`. Execute: enqueue first (503 if queue down, no state change), then move `approved -> executing` unless the worker already did; `pipeline.execute` accepts `approved` or `executing`.
- All state changes go through `app.incidents.state_machine.transition` (IllegalTransition -> 409 `illegal_transition`) and write `audit_events`.
- Nothing is faked: missing prompt cluster / experiment history / policy version => empty arrays, `available:false`, `unavailable_reason`, or null.
- Job enqueue facade: `app.core.queue.enqueue(kind, payload) -> job_id` (delegates to `app.workers.queue.enqueue_job`; `AEO_QUEUE_INLINE=1` runs the handler in-process, used by tests). Kinds are A14's `JOB_KINDS`.
- EventBus (`app.core.events`): `await emit(session_or_none, incident_id, stage, status, message, metadata)` or `emit(incident_id, stage, ...)`; persists `IncidentEvent` (a supplied session is committed so subscribers can see the row), publishes to Redis (`aeo:incident_events:<id>`) with in-process fallback. `subscribe(incident_id, last_event_id)` and `subscribe_global()`.
- `Incident.number` and `IncidentEvent.seq` are assigned in a `before_insert` listener (PG sequence, max+1 on sqlite) so values are loaded after flush in async sessions.

## Known gaps / for others
- `/api/search` (audit #16) not built (stretch). No auth: `X-Actor` is trusted.
- LLM capability state is per-process; the API process only reflects calls made by the API process (worker calls are not visible). Consider persisting the tracker if the Settings screen must show worker-side LLM health.
- Profound capability probe runs on each `/api/system/capabilities` call (5-min client cache applies); with no key it makes no network calls.
- Dev DB hygiene: `python -m app.core.init_db` only creates missing tables; it does not alter existing ones. Alembic (A14) should own that.
- Event `seq` is a global counter; a subscriber reconnecting with `Last-Event-ID` can theoretically miss a lower seq committed after a higher one in a multi-process race. Acceptable for the demo; `/events/history` allows resync.

## Update (lead-requests #2 / A11)
- Dry-run experiments: `/api/experiments` rows now have `dry_run: bool` and `measured: bool`; a dry-run that reached executed/awaiting_verification shows `display_status="Never Measured"` and is excluded from the `running`/`awaiting_measurement`/`verified` summary counts.
- `GET /api/incidents/{id}/interventions`: `cold_start` is true only when `selection_basis == cold_start_prior` (false for `rule_fallback` etc.; still true when nothing is selected).
- `MetricDelta.favorable` (bool|null): direction-aware (competitor/position/lost/inaccuracy metrics are better when lower; null when no change).
- `GET /api/experiments?org_id=` filter added. `InterventionOut.executor` added (execution, else experiment, else proposed_change).
- Test: `tests/api/test_smoke.py::test_dry_run_experiment_never_measured_and_filters`; `tests/api` = 44 passed, 1 skipped.
