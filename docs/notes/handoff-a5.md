# Handoff A5 (LaunchPilot approval / persistence / experiment ledger)

Reference: LaunchPilot (SHA 9fd85c1, **no LICENSE -> architecture only, zero code copied**). See `docs/notes/launchpilot.md`, `docs/notes/third-party-a5.md`.

## What exists
- `backend/app/models/interventions.py`: `Intervention`, `Approval`, `Execution`, `Experiment`, `Observation`, `Reward` per the models contract (Uuid PKs, `JSONType` = JSON with JSONB variant on Postgres, enums stored as lowercase value strings via non-native `Enum`). FKs by table name: `incidents.id`, `hypotheses.id`, `policy_versions.id`.
  - Extras beyond contract: `Approval.requested_by/requested_actor_type/decided_actor_type/requested_change/expires_at`; `Execution.approval_id`; `Experiment.selected_action/selection_basis/cold_start/reason/proposed_change/approved_change/approval_id/approver/execution_id/executor/execution_reference/dry_run/timeline`; `Experiment.code` property -> `EXP-%04d`.
  - `Experiment.number`: Sequence `experiment_number_seq` (Postgres, assigned via nextval in `before_insert` so it is loaded after flush) / max+1 on sqlite.
  - ORM guard: updating a decided `Approval` raises `ImmutableApprovalError` at flush.
- `backend/app/services/approvals.py`: `request_approval(session, intervention, requested_by, actor_type=...)` (idempotent while pending; refuses if already granted; re-request allowed after reject/expire), `decide(approval, status, decided_by, note, modified_change, actor_type="human")` (sync, in place), `decide_approval(session, ...)` (decide + flush + audit event), `expire_stale(session)`, `latest_approval`, `effective_change(approval, intervention)`, `require_executable_approval(session, intervention)` (executor gate; returns None for `observe`; raises `ExecutionNotAuthorized`), `requires_approval`. Errors: `ApprovalError`, `ApprovalImmutable`, `HumanActorRequired`, `ApprovalExpired`, `ExecutionNotAuthorized`.
- `backend/app/experiments/`: `status.py` (`ALLOWED`, `can_transition`, `transition`, timeline logging, guards), `ledger.py` (`open_experiment`, `attach_approval`, `begin_execution`, `mark_executed`, `fail_experiment`, `VerificationWindow` default delay 24h + duration 7d), `verification.py` (`record_observation`, `qualifying_observations`, `evaluate` -> `EvaluationResult`), `outcomes.py` (`historical_outcome_range`, min n default 5, env `AEO_OUTCOME_MIN_N`), `metrics.py`.
- Tests: `backend/tests/unit/test_approvals.py` (11), `backend/tests/unit/test_experiments.py` (22), aiosqlite. Full suite 401 passed at time of writing.

## Behaviours worth knowing (for A10 / A12 / A14)
- Executor (A10) MUST call `require_executable_approval(session, intervention)` and execute `effective_change(approval, intervention)`; then `begin_execution(session, experiment, execution)` and `mark_executed(session, experiment, execution)`. `begin_execution` re-checks the live approval.
- Dry-run executions (`Execution.dry_run=True`): experiment becomes `executed`, flagged `dry_run`, never gets a verification window, observations are refused, `evaluate` returns no reward. Only real executions go to `awaiting_verification`.
- `evaluate(session, experiment)`: only rewards when an observation exists with `observed_at >= verification_window_start` (and after execution). Otherwise status stays `awaiting_verification`, `reward is None`, and `EvaluationResult.window_elapsed` says whether the window end has passed. Reward is computed via `app.learning.compute_reward(before, after, action, risk, weights)` (sync or async); `NoObservation` (unusable observation) leaves the experiment waiting. On success: `after_metrics`, status verified -> rewarded, `Reward` row. Caller (A14 worker) should then call `app.learning.ingest_reward(session, experiment.id)` for the policy update (not called here).
- `open_experiment(session, intervention, decision, incident, before_metrics, evidence_snapshot, context_vector)`: `decision` duck-typed (policy `Decision`, `PolicyDecision` row or dict). `policy_version_id` resolved from intervention, then `decision.policy_version_id`, then `decision.policy_version` string lookup. If the intervention's action differs from the decision's action (human override) propensity is dropped and basis becomes `manual_override`. `before_metrics` must be a non-empty numeric dict (or list of `{metric|name, value|current}`); raises `ValueError` otherwise.
- `historical_outcome_range(session, category, action, min_n=None)` -> `OutcomeRange(n, min_n, metrics{name: Range(low,high,median,n)}, reward)` using p25-p75 of measured deltas (fractions, 0.08 = +8pp) from REWARDED non-dry-run experiments joined to `incidents.category`; `None` when n < min_n (UI: "Insufficient history").
- Observe: needs no approval; experiment can go proposed -> approved via `attach_approval(session, exp, None)`.

## Not done / gaps
- No REST routes (A12 owns `app/api`); suggested mapping: approve/reject/modify -> `decide_approval` then `attach_approval`; execute -> A10 executor; verify -> `evaluate`.
- No Incident state transitions on approval/execution (A2 state machine / A12 service should react).
- `Experiment.after_metrics` is only set at evaluation (not on every observation).
- Alembic migration is A14's (metadata-driven); new sequence `experiment_number_seq` must be created.
- `Execution.status` is a free string (suggested: pending|running|succeeded|failed).
