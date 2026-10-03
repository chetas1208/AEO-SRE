# N2 handoff: outbox, projector, replay/rebuild, audit

## GRAPH MODEL STABLE
Code: `backend/app/graph/model.py` (labels, relationship allowlist, id functions), `events.py` (domain row -> graph plan),
`projector.py` (plan -> Cypher). Every node has `organization_id`, `app="profound-change-guard"`, `projected` (true = real,
false = endpoint stub awaiting its event). Ids are Postgres UUIDs, deterministic uuid5 ids, or org-namespaced strings; never
Neo4j internal ids. Timestamps are ISO-8601 UTC strings with microseconds and `Z` (string order == time order).
Schema additions to N1 (additive, constraints applied live): labels `Incident`, `Intervention`; index `Event.correlation_id`.

### Nodes (label: id property = source; other properties)
| label | id property = source | properties (immutable unless marked *state*) |
|---|---|---|
| Organization | `organization_id` = organizations.id | name, domain |
| Agent | `agent_id` = `<org>:<agent_ref>` | agent_ref, name, origin, source_mode; `profound_agent_id` ONLY for LIVE external agents |
| AgentRun | `agent_run_id` = `<org>:<agent_ref>:<run_ref>` | run_ref, agent_ref, source_mode; `profound_run_id` ONLY for LIVE external runs (SIMULATED keeps `run_ref` only) |
| Event | `event_id` = outbox id (uuid5 of aggregate_type+aggregate_id+event_type+version) | event_type, occurred_at, recorded_at, source (PROFOUND/CHANGE_GUARD/HUMAN/SYSTEM), source_mode (LIVE/SIMULATED/FIXTURE/TEST), correlation_id, aggregate_type, aggregate_id + event specific props |
| ChangeSet | `changeset_id` = change_sets.id | action_type, origin (external/intervention), source_mode, target_key, proposal_digest, reason, risk, agent_ref, intervention_id, occurred_at, claim_count; *state* `status` (latest decision), `last_decision_id`, `state_at` |
| Target | `target_id` = `<org>:<normalized url>` | key, kind="page" |
| Claim | `claim_id` = uuid5(org + normalized text) for proposed claims, canonical_claims.id for canonical | canonical (bool), statement, key (canonical); *state* status, valid_from, valid_until, retired_at |
| PromptCluster | `prompt_cluster_id` = prompt_clusters.id | topic, prompt_count |
| Conflict | `conflict_id` = uuid5(check id, finding index, finding type) | conflict_type, check (1/2/3), severity, decision, reason, check_id, change_set_id, status="open", occurred_at |
| Decision | `decision_id` = change_checks.id | decision (ALLOW/MERGE/DELAY/REQUIRE_REVIEW/BLOCK), eligible_after, guard_version, action_digest, semantic_check, change_set_id, intervention_id, finding_count, conflict_count, source_mode, occurred_at |
| Approval | `approval_id` = approvals.id | intervention_id, requested_by; *state* status, decided_by, decided_at, decided_actor_type, action_digest |
| Experiment | `experiment_id` = experiments.id | code, number, action, incident_id, intervention_id, selection_basis, cold_start; *state* status, executed_at, executor, dry_run, execution_reference, verification_window_start/end, evaluated_at, policy_action, approver, target_key |
| Observation | `observation_id` = observations.id | experiment_id, source, source_run_id, observed_at, metric_keys |
| Outcome | `outcome_id` = experiment_outcomes.id | experiment_id, outcome, observe_outcome, reward_total, causal_confidence, learning_applied, observed_at, evaluated_at |
| PolicyDecision | `policy_decision_id` | selected_action, probability, selection_basis, cold_start, n_related, incident_id, occurred_at |
| PolicyVersion | `policy_version_id` | version, algorithm, n_updates, immutable; **`organization_id` = `GLOBAL`** (policy versions are not org data; org-scoped queries must not filter PolicyVersion by org) |
| Incident | `incident_id` | number, title, category, severity, detected_at; *state* state, priority |
| Intervention | `intervention_id` | action, title, risk, incident_id; *state* selected, selection_basis |

*state* properties are applied only when the event's `state_at` (domain `updated_at` / `created_at`) is not older than the node's
`state_at`, so out-of-order or replayed events can never regress a node.

### Relationships (all carry `organization_id`, `app`)
`Agent-STARTED->AgentRun`; `(AgentRun|Agent)-PROPOSED->ChangeSet`; `(AgentRun|Agent)-EMITTED->Event(CHANGE_PROPOSED)`;
`Event-ABOUT->(the aggregate nodes it concerns)`; `ChangeSet-MODIFIES->Target`; `ChangeSet-ALTERS->Claim`;
`ChangeSet-AFFECTS->PromptCluster` (only real clusters of the same org); `ChangeSet-DERIVED_FROM->Intervention` (origin=intervention);
`Decision-DECIDES_ON->ChangeSet`; `Decision-BASED_ON->Conflict`; `Decision-FOLLOWED_BY->Decision` (previous check of the same ChangeSet -> this one);
`Conflict-ABOUT->Target`; `Conflict-INVOLVES->(ChangeSet|Intervention|Claim)`; `Conflict-PROTECTS->Experiment` (active-experiment contamination);
`ChangeSet-CONFLICTS_WITH->ChangeSet` (duplicate / conflicting target change, edge prop `conflict_id`);
`Approval-ABOUT->Intervention`; `Approval-APPROVES->Decision` (same intervention + same `action_digest`; only when the digest exists);
`(Intervention|Approval|ChangeSet)-EXECUTED_AS->Experiment`; `Experiment-TRIGGERED_BY->Incident`; `Experiment-MEASURES->Target`;
`Experiment-OBSERVED->Observation`; `Experiment-PRODUCED->Outcome`; `Outcome-DERIVED_FROM->Observation`;
`PolicyDecision-USED->PolicyVersion`; `PolicyDecision-SELECTED->Experiment`; `PolicyDecision-ABOUT->Incident`;
`Intervention-ABOUT->Incident`; `Intervention-USED->PolicyVersion`; `Incident-ABOUT->PromptCluster`;
`PolicyVersion-DERIVED_FROM->(parent PolicyVersion | source Experiment)`;
`Event-NEXT->Event` only inside one `correlation_id` (ChangeSet id for external proposals; incident id for everything in an
incident/intervention lifecycle), rebuilt per correlation ordered by (occurred_at, event_id) after each batch.
Never created: `CAUSED_BY`. No Execution/Reward node: they are Events (`CHANGE_EXECUTED`, `REWARD_CREATED`) `ABOUT` the experiment.

### Event types (graph) <- outbox event_type
CHANGE_PROPOSED <- change_set.created; DECISION_CREATED <- change_check.decided; CONFLICT_DETECTED <- change_check.conflicts (only when a non-info finding exists);
APPROVAL_GRANTED / APPROVAL_REJECTED <- approval.changed; CHANGE_EXECUTED / CHANGE_EXECUTION_FAILED <- execution.recorded;
EXPERIMENT_STARTED (status approved) / EXPERIMENT_VERIFIED <- experiment.changed; OBSERVATION_RECORDED <- observation.recorded;
OUTCOME_MEASURED <- outcome.measured; REWARD_CREATED <- reward.created; POLICY_UPDATED <- policy_version.created (only versions with a parent);
INCIDENT_CREATED <- incident.created; CANONICAL_CLAIM_CHANGED <- canonical_claim.changed. TARGET_RESOLVED / CLAIMS_EXTRACTED are
not separate steps in this domain (the Target / Claim nodes and MODIFIES / ALTERS edges inside CHANGE_PROPOSED carry them).
State-only outbox rows (no Event node): organization.upserted, prompt_cluster.upserted, incident.changed, intervention.changed,
policy_decision.created, and approval/experiment rows whose status has no event.

## How events are produced (no domain-file edits)
Instead of editing G1's `service.py`/`approvals.py`/`pipeline.py`, the choke point is the ORM `after_flush` hook registered in
`app/models/graph_outbox.py` (auto-imported, same pattern as `models/ledger_audit.py`): every flush that inserts/changes an
Organization, PromptCluster, Incident, Intervention, ChangeSet, ChangeCheck, Approval, Execution, Experiment (timeline transitions),
Observation, ExperimentOutcome, Reward, PolicyDecision, PolicyVersion or CanonicalClaim writes its outbox rows with a Core
INSERT on the SAME connection/transaction (rollback => no outbox row; commit => always one). A build failure is logged, never raised.
`enqueue_graph_event(session, event_type, aggregate_type, aggregate_id, payload, organization_id, *, version=None)` in
`app/graph/outbox.py` is the explicit helper for any future non-ORM hook (idempotent). Switch off with `GRAPH_OUTBOX_ENABLED=false`.

## Operations
- Outbox `graph_outbox` (migration `0006_graph_outbox`): id (= Event id), event_type, aggregate_type, aggregate_id, organization_id, payload (projection plan), schema_version, created_at, processed_at, attempts, last_error, next_attempt_at, dead_at (dead-letter flag). Append-only except the processing fields (ORM guard + Postgres trigger).
- Projector: `app.graph.projector.drain()`; worker cron `cron_graph_project` every minute (`app/workers/worker.py`); SKIP LOCKED claims; one Neo4j transaction per batch (UNWIND grouped by label / relationship type per organization); outage => rows stay pending with backoff and never count toward dead-lettering; poison rows are isolated per row and dead-lettered after `GRAPH_OUTBOX_MAX_ATTEMPTS` (8).
- Lag / staleness: `await app.graph.outbox.graph_lag()` (backlog, oldest unprocessed age, dead letters, last error, checkpoint stored in `settings` key `graph_projection_checkpoint`), `await is_graph_stale()` (oldest unprocessed row older than `GRAPH_STALE_SECONDS`, default 300). N5: use `is_graph_stale()` for the `graph_context_stale` fallback; `graph_lag()` for the health/capabilities graph block (not wired by N2: those files belong to others).
- `make graph-audit` (read-only; exit 1 on mismatch), `make graph-replay`, `make graph-rebuild` (needs `GRAPH_REBUILD_ALLOW=1` and `--yes`; clears only `app="profound-change-guard"` nodes), `make graph-replay-test`.
- Replay rebuilds the same outbox ids as incremental capture: a transition row per recorded experiment timeline entry and one current-state row for each other mutable aggregate. Intermediate states of mutable aggregates that Postgres no longer records (an incident's earlier states, an intermediate canonical-claim edit) cannot be reproduced; the final graph state is identical, and the audit counts those Event ids as Postgres-side because the outbox is the event log.
- Audit rows (`audit_events`) are not a projection source: every fact they record is in the domain tables the builders read.
