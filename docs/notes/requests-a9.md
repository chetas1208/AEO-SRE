# Requests from A9 (Bandit / Learning)

1. A5 (`app/experiments/verification.py`) / A14: `ingest_reward(session, experiment_id)` is the single writer of the
   `Reward` row and of new `PolicyVersion` rows (decided by A14). `evaluate()` currently also inserts a `Reward`.
   `ingest_reward` is tolerant: it adopts an existing Reward (does not duplicate it) and writes the PolicyVersion once
   (idempotency key = `PolicyVersion.source_experiment_id`, now UNIQUE). It also accepts experiments already marked
   REWARDED that have no derived PolicyVersion. Cleaner end state: `evaluate()` stops creating `Reward`/REWARDED and
   leaves the experiment VERIFIED with `after_metrics` set; the pipeline then calls `ingest_reward`.
2. A12 / settings: operator action mask is read from `settings` table key `policy`, value
   `{"allowed_actions": {"<action>": bool}}` (also accepts `settings.allowed_actions` in config). `observe` is always forced on.
3. Alembic (A14): `policy_versions.source_experiment_id` is a new nullable UNIQUE column; `policy_decisions` is new.
