# Handoff A9 - Bandit / Learning

Owned: `backend/app/policy/**`, `backend/app/learning/**`, `backend/app/models/policy.py`. Tests: `backend/tests/unit/test_policy.py`, `test_reward.py`, `test_ope.py` (51 tests, seeded, pass on Postgres and `AEO_TEST_DB=sqlite`). Requests: `docs/notes/requests-a9.md`. Third-party: `docs/notes/third-party-a9.md`.

No PPO. Contextual bandit only (disjoint LinUCB, Bayesian-linear Thompson).

## Public interfaces
```python
from app.policy import (encode_context, FEATURE_NAMES, UI_FEATURE_NAMES, FEATURE_DESCRIPTIONS, DIM,
    LinUCBPolicy, ThompsonPolicy, PolicyConfig, Policy, ActionScore, Decision, COLD_START_PRIORS, prior_scores,
    resolve_allowed_actions, PolicyStore, next_version)
from app.learning import compute_reward, NoObservation, RewardResult, ingest_reward, ips_estimate, snips_estimate, dr_estimate
```
- `encode_context(incident=None, *, incident_type, visibility_delta, ..., return_details=False) -> np.ndarray[DIM]` (or `ContextVector` with `.to_json()`, `.missing`). Reads an Incident ORM row/dict (`category`, `metrics`, `context`); explicit kwargs win. Missing inputs encode to a documented neutral value and are listed in `.missing` (never invented).
- `Policy.score(ctx) -> list[ActionScore(action, mean, uncertainty, ucb, prior, learned_mean, learned_weight, n_observations, probability)]`; `Policy.select(ctx) -> Decision(action, probability, scores, selection_basis, cold_start, policy_version, n_related, allowed_actions, matched_rules, algorithm, context, note)`; `Policy.probabilities(ctx)`, `Policy.propensity(ctx, action)`, `Policy.update(ctx, action, reward)` (in-memory), `Policy.to_state()/from_state()`.
  `ctx` may be an ndarray, `ContextVector`, `{name: value}`, or the stored JSON `{"names","values"}` / plain list (so `Experiment.context_vector` can be fed straight back).
- `PolicyStore(session, algorithm="linucb", config=None)` (async): `ensure_initial()` (creates cold-start `v0.0.1`), `latest()`, `get(version|uuid)`, `list_versions()`, `load_policy(version=None)`, `save_update(parent, ctx, action, reward, experiment_id)`, `record_decision(decision, policy, incident_id)`, `decide(ctx, incident_id) -> (Decision, PolicyDecision row)` (applies the operator action mask and logs the propensity). The row id for `Intervention/Experiment.policy_version_id` is `policy.version_id` / `PolicyDecision.policy_version_id`.
- `compute_reward(before, after, action, risk, weights=None) -> RewardResult(components, total, weights, missing, raw_deltas)`; raises `NoObservation` (never fabricates).
- `ingest_reward(session, experiment_id) -> IngestResult(reward, policy_version, parent_version, reward_row)`: **single writer** of the `Reward` row and of new `PolicyVersion` rows. Flushes, does not commit. Raises `NoObservation` (nothing written), `NotRewardable`, `AlreadyRewarded` (idempotent: key is `PolicyVersion.source_experiment_id`, UNIQUE). If a Reward row already exists (written by the verification step) it is adopted, not duplicated. Sets the experiment to REWARDED, audits `policy_updated`.
- OPE: `ips_estimate / snips_estimate / dr_estimate(actions, rewards, propensities, pi_e[, q_hat])`, `fit_reward_model`, `evaluate_policy(policy, ctxs, actions, rewards, props)`, `load_logged_feedback(session)`, `filter_valid_log(bases)` (drop `rule_fallback`/`manual_override`; they have no valid propensity).

## Behaviour
- Scores per allowed action: `w = n_a/(n_a+prior_strength)`; `mean = w*learned + (1-w)*prior`; `ucb = mean + uncertainty`. Priors are hand-set config (below), not learned.
- Propensity (stored for OPE): LinUCB `(1-eps)*softmax(ucb/T) + eps/K` (defaults T=0.1, eps=0.05); Thompson `(1-eps)*P(argmax)` from a fixed seeded set of 512 normal draws (common random numbers, exact deterministic function of state+context) `+ eps/K`. Every allowed action has p >= eps/K. Action is sampled from it with a seeded RNG (`PolicyConfig.seed`).
- `selection_basis`: `cold_start_prior` until `min_related` (default 5) verified experiments of the same incident type have updated the policy, then `learned_policy`; `rule_fallback` (prior argmax, else observe; probability 1.0, excluded from OPE) if scoring raises.
- `observe` is always an allowed candidate (mask forced). Mask source: `PolicyConfig.allowed_actions`, else `settings` table row `policy` -> `{"allowed_actions": {action: bool}}` (via `PolicyStore.decide`), else `settings.allowed_actions`, else all.
- Versions: `v0.0.1` is the cold-start version (0 updates). Every reward ingestion INSERTs a new row `v0.0.N+1` with `parent_id`, `n_updates`, `source_experiment_id`, serialized `state` (A, b per action, counts, config) and `priors`. ORM events raise `ImmutableVersionError` on update/delete.
- Reward: `0.35*vis + 0.30*cit + 0.20*acc + 0.15*comp - action_cost - risk_penalty`, each component `tanh(delta/0.10)` of the absolute change of fractional metrics (`visibility`, `citation_share`, `accuracy`, `competitor_share`; values >1 read as percentages; competitor sign inverted). `ACTION_COSTS` and `RISK_PENALTIES` are config in `app/learning/reward.py`. Missing metric -> component 0 and listed in `missing`; no comparable metric or no `after` -> `NoObservation`. Total clipped to [-1, 1].

## Feature list (fixed order, `FEATURE_NAMES`, dim 23)
`visibility_delta, citation_delta, accuracy_delta, competitor_delta` (relative change, clip [-1,1]; competitor + = gaining), `prompt_volume` (log1p(v)/log1p(10000), [0,1]), `buyer_intent, source_authority, persona_value, action_cost, factual_conflict` ([0,1]), `source_freshness` (given, or exp(-age_days/180)), `owned_source, third_party_source, content_exists` (0/1; content_exists unknown = 0.5), `historical_success` (unknown 0.5; helper `smoothed_success_rate`), `incident_type=<IncidentCategory>` x7 one-hot, `bias` (1.0; hidden from UI via `UI_FEATURE_NAMES`). Descriptions for UI: `FEATURE_DESCRIPTIONS`. Unknown defaults: 0.5 for quality scores/content_exists, 0 otherwise.

## Cold-start priors (`COLD_START_PRIORS`, per-action max over matched rules; none matched -> default observe 0.20, others 0.10; unlisted actions in a matched rule 0.5x default)
| rule | when (all) | prior scores |
|---|---|---|
| owned_outdated_page | owned_source>=0.5, content_exists>0.5, source_freshness<0.4 | update_existing_page 0.50, structured_data 0.20, create_faq 0.20, observe 0.05 |
| owned_factual_conflict | owned_source>=0.5, content_exists>0.5, factual_conflict>=0.5 | update_existing_page 0.50, create_faq 0.20, structured_data 0.15 |
| third_party_misinformation | third_party_source>=0.5, factual_conflict>=0.5 | publisher_outreach 0.50, create_canonical_page 0.25, create_faq 0.20, observe 0.05 |
| missing_canonical_info | content_exists<0.5, prompt_volume>=0.4 | create_canonical_page 0.50, create_faq 0.30, structured_data 0.10 |
| competitor_displacement | competitor_delta>=0.2, visibility_delta<=-0.1 | create_comparison_content 0.45, update_existing_page 0.25, create_faq 0.20 |
| lost_citation_owned_content_ok | citation_delta<=-0.15, content_exists>0.5, owned_source>=0.5, source_freshness>0.5 | structured_data 0.40, create_faq 0.30, update_existing_page 0.20 |
| minor_low_volume_fluctuation | prompt_volume<=0.4, visibility_delta>=-0.15, factual_conflict<0.5 | observe 0.50 |

## UI mapping
Alternatives list (UI 23): `Decision.scores` (use `mean` or `probability`; label "candidate interventions"). Policy surface (UI 32): `policy_version`, `selection_basis`, `n_related`, `cold_start`, score. Basis text (UI 63): cold-start -> "Cold-start prior - no related verified experiments yet"; learned -> "Policy vX - learned from {n_related} related experiments". Expected outcome (UI 66) is NOT produced by the policy; derive ranges from rewarded experiments with n, else say insufficient history.

## Validation (not used to train AEO)
- `python -m app.learning.synthetic_validation` (numpy only, runs in the backend venv): synthetic linear simulator; LinUCB/Thompson cumulative regret << random; full policy classes' greedy regret drops >99%.
- `python -m app.learning.obp_validation [--out ...]`: needs `obp`, which is NOT in the backend venv (it pulls torch/pandas). obp 0.4.1 only works with pandas<2, so it was run in a separate python 3.11 venv (`uv venv --python 3.11; uv pip install obp "pandas<2" "numpy<2" "scikit-learn<1.4" "sqlalchemy[asyncio]" pydantic pydantic-settings structlog`). It uses the OBD random-policy "men" sample (10k rows) bundled in the obp package (no download needed). Falls back to the synthetic simulator if obp is missing. Results: `docs/notes/a9-obp-validation.json` (passed=true):
  - OPE parity: our IPS/SNIPS/DR equal OBP's IPW/SNIPW/DR on the same OBD rows to ~1e-16.
  - State parity: our per-action A^-1, b equal OBP LinUCB after 2000 streamed rows (max err 5.6e-16); argmax of UCB agrees on 300/300 rows.
  - Offline learning on OBD (train/test halves): our greedy LinUCB policy value equals OBP's (0.0068 vs random 0.0052) - but CTR is ~0.46% on 10k rows, ESS ~147, so this is a smoke test, not a performance claim.
  - OBP `SyntheticBanditDataset` ground truth (3 seeds): ours and OBP's LinUCB/LinTS all beat random by >3x in cumulative regret; ours is comparable to OBP.
  Caveat to state in README: validates the machinery only; no domain transfer to AEO.

## Known gaps
- `encode_context` metric extraction from `Incident.metrics` is best-effort (keys `metric|name|key|kind|label` containing visibility/citation/accuracy/competitor; `delta_pct`/`delta`/`value+baseline`); callers with better data should pass explicit kwargs. A3/A12 should supply `incident.context` entries (owned_source, third_party_source, content_exists, factual_conflict, source_age_days, buyer_intent, persona_value) from the evidence step; otherwise they are flagged missing and neutral.
- The `action_cost` feature is an incident-level effort estimate, not per-action (disjoint models make per-action cost redundant; per-action cost is in the reward).
- Related-experiment count is per incident type, not a similarity kernel.
- Thompson propensity uses 512 draws (resolution ~0.002); eps floor guarantees support.
- Concurrent ingests race on the version string; handled by savepoint + retry (3 attempts).
