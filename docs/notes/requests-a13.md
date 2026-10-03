# Requests / findings from A13 (Test / Reliability) -> owners; A14 triages

Every item has a failing (or guarding) test. Run: `cd backend && .venv/bin/python -m pytest -q <test id>`.
Resolved during the build (guarded by regression tests now): pipeline.execute signature/dry-run bug (A14),
policy context ignoring `key`-named metrics (A9), hypotheses not citing Profound metric evidence so the gate could
never confirm (A14/A3), Experiment.number lazy-load in async (A5).

## P1 - false-success / safety

### 1. `ingest_reward` can reward without a qualifying measured observation  (owner A9 `learning/ingest.py`, with A5)
Tests (all fail today): `tests/integration/test_false_success_hunt.py::`
`test_observation_taken_before_execution_cannot_produce_a_reward`,
`test_observation_inside_profound_lag_window_cannot_produce_a_reward`,
`test_dry_run_experiment_can_never_be_rewarded_even_with_metrics`,
`test_valid_observation_after_window_is_rewarded_and_after_metrics_persisted`,
`test_rewarded_status_requires_after_metrics_invariant`.
- `backend/app/learning/ingest.py:97-112`: `after = exp.after_metrics or _latest_observation_metrics(obs_rows)` takes the newest
  Observation of ANY time. It ignores `verification_window_start`, `executed_at` and `exp.dry_run`. The worker job
  `calculate_reward` / `update_policy` call it directly (`app/workers/jobs.py`), so a stray or early observation rewards the
  experiment and writes a new PolicyVersion. (Via `pipeline.verify` the window is respected, but `ingest_reward` is a public API.)
- `ingest.py:126`: `exp.status = ExperimentStatus.REWARDED` is assigned directly, bypassing
  `app.experiments.status.transition` (which requires `after_metrics`); `after_metrics` and `evaluated_at` are never written when the
  reward comes from an Observation row, so a rewarded experiment can show no "after" in the UI.
Fix: in `ingest_reward` reject `exp.dry_run`; build `after` only from `app.experiments.verification.qualifying_observations(session, exp)`
(window start and > executed_at); raise `NoObservation` when empty; set `exp.after_metrics = jsonable(after)` and move through
`experiments.status.transition(... VERIFIED then REWARDED)` so the guard and timeline apply; set `evaluated_at`.

### 2. [RESOLVED, guarded by test] Policy can select an action that has no executable change  (owner A14 `pipeline.propose` / A10 `interventions/propose.py`)
Test: `test_false_success_hunt.py::test_selected_intervention_is_executable_or_observe` (passes at last full run).
When the owned page has no grounded facts, the cold-start policy still selects `create_comparison_content`; its template returns
`None` (`interventions/templates.py:create_comparison_content`), so `Intervention.proposed_change` is NULL, a human is asked to approve
it, and `execute` ends in `ExecutionRefused: no executable proposed change` (incident FAILED path). Fix: after
`plan_candidates`, if the policy's choice has no change, either mark the intervention `selected=False` with a visible
`unavailable_reason` and fall back to the best executable candidate (selection_basis = rule_fallback, probability recorded
for the action actually chosen), or restrict the policy's `allowed` set to executable candidates before `select`.

## P2 - hardening

### 3. [RESOLVED, guarded by test] Evidence gate can be bypassed in the state machine  (owner A2 `incidents/state_machine.py:159-163`)
Test: `test_false_success_hunt.py::test_state_machine_cannot_confirm_root_cause_without_gate_result` (passes since A2's fix).
`transition(inc, ROOT_CAUSE_CONFIRMED, actor, reason)` with `gate=None` succeeds. Pipeline passes a gate view, but any other caller
(or a future API route) can confirm without the gate. Fix: require `gate is not None and gate.confirmed` for that target.

### 4. [RESOLVED, guarded by test] `executor.authorize` accepts an approval with unknown actor type  (owner A10 `interventions/executor.py:132`)
Test: `tests/integration/test_executor_db.py::test_executor_authorize_requires_explicit_human_actor` (passes since A10's fix).
`getattr(approval, "decided_actor_type", "human") not in (None, "human")` lets `decided_actor_type=None` through;
`approvals.require_executable_approval` (the DB gate used by `run_execution`) correctly demands `== "human"`. Fix: `!= "human"` -> refuse.

### 5. "human" is only an assertion (owner A12, product decision)
`api/routes/interventions.py::_decide` hard-codes `actor_type="human"` and takes the actor name from the unauthenticated
`X-Actor` header, so any HTTP client can approve and trigger execution. Acceptable for the hackathon demo; state it in the README
(approval safety = process control, not authentication) or require a configured shared secret header for approve/execute.

## P3 - notes / contracts

6. Emit callable shapes differ: `collect_evidence(..., emit)` expects `emit(stage, status, message, meta)` but BUILD_BRIEF says
   `EventBus.emit(incident_id, stage, ...)`. pipeline binds it correctly; keep this in the docs. (`tests/helpers.EmitRecorder` accepts both.)
7. `pipeline.verify(force=True)` / `POST /api/experiments/{id}/verify?force=true` bypasses the Profound lag window (observations
   between execution and window start count). Intended as an operator override; the reward then reflects data inside the 24-48h lag.
   Consider recording `forced=true` on the Observation/Reward row so provenance shows it.
8. `_profound_evidence` stores the detector confidence as the evidence `support_score` (`score_basis=detector_confidence`). It is
   labeled, but the gate's aggregate confidence therefore partly measures the detector itself. Fine for now; mention in README limits.
9. Test infra: `Experiment.number` and other sequence defaults are fine on Postgres now; the sqlite fallback relies on max+1 listeners.
10. `docs/notes` note for A14: Makefile `test` target runs the whole backend suite; the full suite takes ~2 min because several tests
    wait on the unreachable-redis path (REDIS_URL is pinned to a closed port in tests/conftest.py to force the in-process fallbacks).
