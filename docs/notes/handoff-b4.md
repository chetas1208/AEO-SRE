# Handoff B4 (policy, experiments, verification, learning)

Scope: backend deep campaign, agent B4. Fixture Experiment 1 (OBSERVE, awaiting_verification) was never verified, rewarded
or altered; every test uses a test DB and a fixed/injected clock. `make eval` 25/25. Ruff clean.

## What changed (by task)

1. **Context from persisted state** - `app/policy/context.py::build_policy_context(session, incident)` reads incident
   (severity, priority, priority components, metric deltas), evidence rows, hypotheses, signals and historical memory.
   A priority component whose `source == "default"` is NOT presented as a measurement (it encodes to the documented
   neutral value and is listed in `PolicyContext.missing`). Feature schema is versioned (`ctx-v2`, see below) and
   persisted on every `PolicyVersion` (`priors.meta.feature_schema`, state `feature_schema`), `PolicyDecision`
   (`context_vector.schema`, `meta.feature_schema`) and the experiment context vector.
2. **Action mask before scoring** - `app/policy/mask.py`: `update_existing_page` needs a relevant owned page,
   `publisher_outreach` needs a third-party (external) source, `create_canonical_page` needs a canonical gap (evidence was
   collected and no owned page exists), `observe` always eligible; rules fail closed. `Policy.select/score/probabilities/
   rule_fallback(eligible=..., masked=...)`: masked actions are never scored and never selectable (tests: 300 seeds x
   LinUCB/Thompson). The pipeline fallback ("no executable change drafted") also cannot pick a masked action. Taxonomy
   (`ActionType`) unchanged.
3. **PolicyDecision record** - `record_decision` stores: policy version id, context vector + schema, eligible actions
   (`allowed_actions`), masked actions with reasons (`meta.masked_actions`), per-action scores, selected action,
   propensity, selection basis, `meta.selection.uniform_draw` (the inverse-CDF draw), hyperparameters, timestamp.
   `policy.store.reproduce_decision(session, row)` recomputes the decision from persisted version + context + eligibility
   + draw and returns `match` (tested, incl. rule-fallback and constrained-to-observe rows).
4. **PolicyVersion** - immutable (ORM guard existed; unchanged). `priors.meta` now carries algorithm, hyperparameters,
   parent version/id, rewarded-experiment count (== `n_updates`), `prior_config_hash` (sha256 of explicit priors + config),
   feature schema, `cold_start` flag and an honest note ("hand-set explicit priors only; learned nothing").
   No schema change was needed (stored inside the existing JSON).
5. **OBSERVE semantics** - see "Decisions-ready text" below.
6. **Human override / rejection** - `Experiment.policy_action` (what the policy chose), `selected_action` (what was
   executed), `override_reason`, `override_by` (columns added by B1). The outcome/reward is attributed to
   `selected_action` only; the policy propensity is dropped (`policy_probability` NULL, basis `manual_override`).
   Rejection never creates a Reward/PolicyVersion; `app/learning/feedback.py` exposes rejection rate + reasons per action as
   labelled supervised feedback (`kind: supervised_feedback_not_reward`) and the list of overrides.
7. **Hypothesis + metrics declared before activation** - `app/experiments/spec.py`; `open_experiment` writes
   `Experiment.spec` = `{if_action, because_root_cause, then_metric, direction, window_hours, delay_hours, primary_metric,
   secondary_metrics, declared_at, statement, observe, spec_hash}`; the statement reads "IF we apply X BECAUSE <root cause>
   THEN <metric> SHOULD increase AFTER 48h from execution (measured within a 216h verification window)". The primary
   metric must have been measured in the before-state (SpecError otherwise); chosen deterministically per incident
   category. `attach_approval` / `activation_check` refuse an experiment without a valid spec. Immutability after
   activation is B1's ORM guard on `spec` (tested).
8. **Verification window = one service** - `app/experiments/window.py` (see semantics below); API `/verify`, worker
   `due_experiment_ids`, `pipeline.verify`, `devtools/experiment_status`, `qualifying_observations`, ledger, manual and
   executor paths all call it (`window_from_settings()`, `attempt_allowed`, `measurement_eligible`, `use_clock`).
   Injected clock (`app.core.clock`: SystemClock UTC in prod, FixedClock in tests via `use_clock`). The only code that sets
   `Experiment.after_metrics` is `experiments.verification.apply_measurement` (also `evaluate` and the legacy branch of
   `ingest_reward`, all inside `verification_path()`); `experiments/guards.py` raises `UnverifiedAfterMetrics` for any
   other assignment on a persisted experiment (plus B1's write-once guard). Fixed a latent bug: GitHub-executor path used
   to overwrite the window after `awaiting_verification` (now the window is correct at `mark_executed`).
9. **Outcomes** - `app/experiments/outcome.py` (methodology below); `ExperimentOutcome` row (B1 table) holds outcome,
   observe label, reward total (NULL when no learning), measured components only, confounders, causal confidence,
   `learning_applied`, `policy_version_id`, measurement timestamp, methodology + causal statement. `Reward` rows are only
   written when learning applies; failed/dry-run executions raise `NotRewardable` (no outcome at all).
10. **Historical memory** - `app/learning/memory.py::retrieve_similar` (structured cosine/L1 over stored ctx vectors gated
    by incident category + lexical TF-IDF over incident/hypothesis text; NOT neural embeddings, stated in `method`).
    Returns action, outcome, reward, causal confidence, verification delay (hours), similarity. `MemorySummary.features()`
    -> numbers only (`memory_similar_n`, `memory_mean_reward`, `memory_favorable_rate`, `memory_uncertainty`,
    `historical_success`); excluded: own incident, dry runs, anything after `as_of`.
11. **Policy update** - `ingest_reward` creates PolicyVersion N+1 inside one savepoint with the outcome and reward rows;
    parents are never mutated; an older-schema parent is zero-padded into the current schema (recorded in meta).
    Controlled learning evaluation in `tests/b4/test_policy_core.py` (labelled controlled evaluation, not live learning):
    repeated favorable -> preference/propensity rises; repeated unfavorable -> falls (LinUCB and Thompson).
12. **Exactly-once** - experiment row lock (`with_for_update`) + `UNIQUE(experiment_outcomes.experiment_id)` +
    `UNIQUE(rewards.experiment_id)` + `UNIQUE(policy_versions.source_experiment_id)`; the loser gets `AlreadyRewarded`
    (4 concurrent workers -> 1 learned, 3 already, 1 reward, 1 outcome, 2 versions; tested on Postgres).
    `record_observation` is idempotent on B1's `uq_observations_source_snapshot`; `pipeline.verify` derives a stable
    `source_run_id` from the signal ids, so a re-run stores one observation.
13. **Collision** - `app/experiments/collision.py` (semantics below). `pipeline.execute` calls `ledger.activation_check`
    before the package/execution; the conflicting experiment is refused (PermanentError + `experiment.collision` event).
14. **ManualExecutor** untouched and still the default: approved -> activation_check -> package -> human `record_executed`
    -> awaiting_verification; OBSERVE activation is itself the execution.

## Decisions-ready text (for B5 to merge into Decisions.md)

### D-B4-1 Feature schema ctx-v2 (additive, versioned)
ctx-v2 appends nine scalars and seven root-cause one-hots AFTER the ctx-v1 layout (bias stays at index 22), so every
v1 index is stable. Added: `severity`, `priority`, `evidence_confidence`, `root_cause_confirmed`, `platform_breadth`,
`memory_similar_n`, `memory_mean_reward`, `memory_favorable_rate`, `memory_uncertainty`, `root_cause=<layer>`.
A PolicyVersion keeps the schema it was trained on and scores only those features; `save_update` zero-pads an older
parent into the current schema (A -> blockdiag(A, ridge I), b -> [b, 0]) so the learned statistics stay exact. Unmeasured
inputs encode to documented neutrals and are reported in `missing`. LinUCB/Thompson, priors, propensities unchanged.

### D-B4-2 Eligibility mask is applied before scoring and fails closed
Masked actions get no score, no probability and cannot be sampled or used as a fallback. `observe` is never masked.

### D-B4-3 Verification window semantics (single service, `app/experiments/window.py`)
`window_start = executed_at + verification_delay_hours` (default 48h, Profound lag); `window_end = start + 7d`.
A verification attempt is allowed iff `now >= window_start` (start INCLUSIVE: start-1s rejected, start and start+1s allowed).
A measurement is eligible iff `observed_at >= window_start` (inclusive), `observed_at > executed_at`, `observed_at <= now`;
`observed_at` is the SOURCE timestamp, never the request time. A measurement after `window_end` is still eligible but
flagged `late` (a stalled worker must not strand an experiment). `now` comes from an injected Clock. Dry runs never verify.

### D-B4-4 Outcome methodology (OBSERVE and interventions)
Label from the PRE-DECLARED primary metric: `gain` = (after - before) in the declared direction; `gain >= 0.02` FAVORABLE,
`gain <= -0.02` UNFAVORABLE, otherwise NEUTRAL (2 percentage points = noise band). INCONCLUSIVE when the primary metric is
missing before/after, the reward is not computable, or a HARD confounder makes the movement unattributable (another
intervention overlapped the window on the same target/cluster; platform-wide movement explains >= 80% of the change; an
OBSERVE baseline was disturbed by an intervention). Soft confounders (competitor share moved, platform movement < 80%, new
prompt in the cluster, owned page changed elsewhere, new external/competitor source) are stored and lower causal confidence.
Causal confidence is `low` (any confounder or neutral) or `medium` (measured change, none detected); never `high`; every
statement ends "before/after association, not a demonstrated cause". No formal causal claim anywhere.

OBSERVE is a deliberate no-remediation monitoring experiment (activation = execution; no approval, no package). Its outcome
label: `self_recovery` (gain >= band) -> FAVORABLE, `persistent` (|gain| < band) -> NEUTRAL, `worsened` (gain <= -band) ->
UNFAVORABLE, `inconclusive` -> INCONCLUSIVE. OBSERVE's reward is `compute_reward` with action cost 0: it is the policy's
no-action baseline for similar contexts ("what happened while we did nothing"), NOT a control group for other actions;
positive rewards of other actions can include the same background recovery and the system does not claim otherwise.

### D-B4-5 Reward mapping
Reward = existing `compute_reward` (visibility .35, citation .30, accuracy .20, competitive .15, minus action cost and risk
penalty) over the metrics actually available; components persisted separately and metrics not measured are omitted (not
stored as 0). FAVORABLE/UNFAVORABLE/NEUTRAL feed their reward to the bandit; INCONCLUSIVE writes the outcome row only (no
Reward, no PolicyVersion; the experiment stays `verified`); failed or dry-run executions produce no outcome. Rejection is
supervised feedback, never an environment reward.

### D-B4-6 Collision and OBSERVE coexistence
An experiment may not be activated while another non-observe experiment (approved/executing/executed/awaiting_verification,
real executions only) is active on the same `target_key` or the same (org, prompt-cluster) scope: both would move the same
cluster metrics and cannot be separated later. The second one is refused, not queued. OBSERVE coexists with everything and
with other OBSERVEs; an OBSERVE whose scope receives a real intervention during its window is recorded INCONCLUSIVE
(`intervention_during_observe`). If a human applies two overlapping interventions anyway, both outcomes are INCONCLUSIVE
(`overlapping_intervention`).

## Schema requests (docs/notes/schema-requests.md): all delivered by B1
`experiments.spec`, `policy_decision_id`, `policy_action`, `override_*`, `target_key`; table `experiment_outcomes`;
observation uniqueness; FKs. B4 needs no further migration. New tables are in B1's models; B4 files only read/write them.
NOTE for B1/B5: ORM status guard validates each status change against the persisted status, so two transitions need a
flush in between (`ledger.mark_executed` now flushes).

## Tests added (`backend/tests/b4/`)
`test_window.py` (boundaries, clock, source timestamps), `test_outcome.py` (labels, observe, confounders, spec),
`test_policy_core.py` (schema, mask, replay, upgrade, cold start, controlled learning evaluation), `test_db_loop.py`
(Postgres: persisted-state context, mask from evidence, decision reproduction, version immutability/N+1, spec immutability,
after_metrics guard, window boundaries via apply_measurement, favorable/inconclusive/observe outcomes, exactly-once +
4-way concurrency, collision + OBSERVE coexistence, override, memory, rejection feedback, verification idempotency).
Edited existing tests only where behaviour legitimately changed: `tests/unit/test_reward.py` now injects a clock for a
measurement stamped after the real "now".

## Gaps / honest limits
- Inconclusive experiments stay `verified` (no `rewarded` transition) and the incident stays `verified`; the API only
  stops showing "awaiting reward" for them. A dedicated terminal state/UI label belongs to B1/B5.
- The API `ExperimentDetail` does not yet expose the outcome row / spec / override (schema is B5's); data is persisted.
- Memory similarity is lexical TF-IDF + structured, not embeddings; with 0 finished experiments all memory features are
  neutral (similar_n 0, uncertainty 1).
- Confounder detection only sees what is persisted (other-cluster signals, evidence rows, prompt-cluster updated_at);
  absence of data is "not detected", not "no confounder".
- The no-op noise band (0.02) and the 80% platform-movement rule are hand-set constants, not calibrated on real Profound data.
- Fixture dev DB still on migration 0001: `policy_versions` there are ctx-v1 (dim 23); the first real update will pad to ctx-v2.
