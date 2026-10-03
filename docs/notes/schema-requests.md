# Schema requests (campaign). Append only; owner of the request names itself; B1 (A1) regenerates migrations.

## B4 (policy / experiments / learning) -> B1

All JSON columns use `JSONType` (JSON + JSONB variant). Nothing here is needed for the fixture row (all nullable / defaulted;
the fixture experiment must stay byte-identical, so NO backfill and NO server default that rewrites existing rows).

1. `experiments.spec` JSONType NULL. Pre-activation hypothesis + metric declaration, written once at proposal time:
   `{"if_action","because_root_cause","then_metric","direction","window_hours","primary_metric","secondary_metrics":[...],
   "spec_hash","declared_at"}`. IMMUTABLE once `status` leaves `proposed` (ORM guard like Approval; change after activation
   -> raise). B4 sets it in `ledger.open_experiment`.
2. `experiments.policy_decision_id` Uuid NULL FK policy_decisions.id (SET NULL) + `experiments.policy_action` String(48) NULL +
   `experiments.override_reason` Text NULL + `experiments.override_by` String(255) NULL. Human override record: policy action vs
   the action actually executed (`selected_action`). Immutable after activation.
3. New table `experiment_outcomes` (one row per experiment, UNIQUE experiment_id, FK experiments.id CASCADE):
   `id Uuid PK`, `experiment_id`, `outcome String(16)` in (favorable|unfavorable|neutral|inconclusive),
   `observe_outcome String(24) NULL` (self_recovery|persistent|worsened|inconclusive; OBSERVE only),
   `reward_total Float NULL` (NULL = no policy update), `components JSONType`, `confounders JSONType` (list),
   `causal_confidence String(16)`, `learning_applied Boolean`, `policy_version_id Uuid NULL` (the N+1 version it produced),
   `observed_at DateTime(tz) NULL` (measurement timestamp from the source), `evaluated_at DateTime(tz)`, `methodology JSONType`,
   `created_at`. Append-only (no UPDATE). Keep `rewards` table unchanged: a `Reward` row is written ONLY when learning applies.
4. DB-level exactly-once (B1 owns, B4 services rely on): `UNIQUE(rewards.experiment_id)` exists; `UNIQUE(policy_versions.source_experiment_id)`
   exists; please add partial unique index "at most one ACTIVE (approved/executing/executed/awaiting_verification) non-observe
   experiment per (incident_id)" is NOT wanted (collision is cross-incident, enforced in the service by target key). Instead
   request: `experiments.target_key String(512) NULL` indexed (normalized org/page-or-source-or-cluster the intervention touches),
   so collision lookup is an index scan. B4 fills it.
5. `policy_versions`: no new columns needed (hyperparameters/prior hash/feature schema live inside existing `priors`/`state`
   JSON; rewarded-experiment count == `n_updates`). If B1 prefers real columns: `feature_schema_version String(16)`,
   `prior_config_hash String(64)`, `hyperparameters JSONType` -- tell B4 and B4 will read them first.

Status (B4): items 1-4 DELIVERED by B1 (columns, experiment_outcomes, target_key) and consumed by B4; item 5 needs nothing (JSON). No further B4 schema requests.

## B3 (model runtime) -> B1

Optional, NOT blocking: per-call model metadata is currently persisted inside `incidents.context["llm"]["calls"]` (list of
`ModelCallMeta` dicts: provider, model, protocol, purpose, prompt_version, latency_ms, attempts, input/output tokens, ok,
error_kind, cached, at) and `hypotheses.produced_by` (`llm:<protocol>:<model>@<prompt_version>`), plus structlog `model.call`.
If a queryable ledger is wanted: table `model_calls` (`id` Uuid PK, `incident_id` Uuid NULL FK incidents.id SET NULL,
`provider` String(64), `model` String(128), `protocol` String(32), `purpose` String(48), `prompt_version` String(64),
`latency_ms` Integer, `attempts` Integer, `input_tokens` Integer NULL, `output_tokens` Integer NULL, `ok` Boolean,
`error_kind` String(32) NULL, `cached` Boolean, `at` DateTime(tz) index). No prompt text, outputs or reasoning are ever stored.

## B2 (Profound / public evidence / investigation) -> B1

All of this is currently stored WITHOUT schema changes (Setting rows / JSON) and works today; promote to real columns/tables
only if B1 wants queryability or constraints. B2 code reads/writes the JSON form, so nothing breaks if these stay unbuilt.

1. `ingestion_runs` table (replaces Setting keys `profound.runs.<org>` / `profound.last_sync.<org>`; record shape already identical):
   `id Uuid PK`, `org_id FK`, `provider String(32)`, `source_mode String(8)` (LIVE|REPLAY|FIXTURE|TEST), `stage String(16)`
   (monitoring|deep|full), `started_at`, `completed_at`, `status String(16)`, `records_seen/records_normalized/records_created/
   records_updated Integer`, `error_count Integer`, `checkpoint JSONType` ({surface: ET end date}), `window JSONType`, `error Text NULL`.
   Index `(org_id, provider, started_at desc)`.
2. `signals.source_mode String(8) NOT NULL DEFAULT 'LIVE'` (today `raw["source_mode"]`, also `raw["ingestion_run_id"]`). Backfill
   rows with `source='dev_fixture'` -> FIXTURE (do NOT touch anything else). Fixture experiment row is unaffected.
3. `prompt_cluster_versions` table: `id`, `org_id`, `version Integer`, `embedder String(64)`, `threshold Float`, `content_hash String(32)`,
   `membership JSONType`, `changes JSONType`, `created_at`; UNIQUE(org_id, version). (Today: Settings `clusters.current.<org>` /
   `clusters.history.<org>`.)
4. `hypotheses.cause String(48) NULL` (taxonomy value, today `incidents.context.hypotheses_meta[hid].cause`) and
   `hypotheses.assessment JSONType NULL` (confidence features/caps/blockers; today `incidents.context.gate[hid].assessment`).
5. `evidence.source_category String(16) NULL` (OWNED|COMPETITOR|THIRD_PARTY|COMMUNITY|UNKNOWN) and `evidence.normalized_hash String(64) NULL`
   (today in `evidence.raw`). Enum `EvidenceType` is untouched (still owned/competitor/external/profound/inference).
6. Incident signature: B2 now writes `incidents.context["signature"]` = {incident_type, family, topic, cluster_id, platform, persona,
   competitor, competitors, primary_metric, direction, metrics, signals, signature_key}. `fingerprint.py` reads incident_type/topic/
   persona/platform/competitors from it; no new column needed. If B1 wants the scoped key as the fingerprint input, use `signature_key`.

## B1 -> B2 / B4 / B5 (status, 2026-10-03)

IMPLEMENTED in models + migration `0002_domain_integrity_constraints` (applied to dev DB `aeo`, fixture rows byte-identical;
`alembic check` clean; fresh-DB upgrade works). NOTE: I did NOT regenerate 0001 (a regenerated 0001 has the same revision id, so the
dev DB at 0001 would never receive the new columns). 0002 is a pure incremental revision on top of the original 0001. B5: do not
run `scripts/regen_initial_migration.sh` unless you also drop+recreate the dev DB (that would delete the fixture).

B4 requests (all done): `experiments.spec`, `policy_decision_id` (FK SET NULL), `policy_action`, `override_reason`, `override_by`,
`target_key` (indexed), table `experiment_outcomes` (UNIQUE experiment_id; FK **RESTRICT** not CASCADE: history is never
cascade-deleted; append-only ORM guard). Frozen after activation by the ORM guard: spec, policy_decision_id, policy_action,
override_*, target_key, before_metrics, evidence_snapshot, context_vector, policy_version_id, policy_probability, alternatives,
selected_action, selection_basis, cold_start, proposed_change, approved_change, approval_id, approver. after_metrics: write-once.
Status changes must go through `experiments.status.transition` (timeline-checked chain), otherwise `IllegalExperimentTransition`.

For B2 (detector / ingestion), no code change is REQUIRED, but please use these:
- `incidents.fingerprint` (String 64, nullable) is filled by an ORM `before_insert` hook (`app/incidents/fingerprint.py`) for any
  Incident whose `context["signature"]` exists. Partial UNIQUE index `uq_incidents_open_fingerprint` (non-terminal states only).
  Optional hints in `context["signature"]` that sharpen the key: `persona`, `platform` (or `engine`), `sources`; plus the existing
  `incident_type`, `topic`, `competitors`. Time bucket = UTC day of `first_observed_at`.
  The detector's own (family, cluster) dedup stays primary; on a concurrent race the loser now gets `IntegrityError`, so wrap the
  `session.add(inc); flush` in `begin_nested()` and treat IntegrityError as "duplicate, skip".
- `signals.idempotency_key` (<=128) + `signals.provider_run_id` (<=128, indexed). Filled by an ORM hook from
  `raw["dedupe_key"]` / `raw["provider_run_id"]`. UNIQUE(org_id, source, idempotency_key), NULLs never collide. If Profound gives
  a stable run/report id, put it in `raw["provider_run_id"]` (key becomes `run:<id>:<dedupe_key>`). Concurrent ingests of the same
  org can now hit IntegrityError on insert: wrap the batch insert in `begin_nested()` and re-read.
- `observations.source_run_id` (default ""), UNIQUE(experiment_id, source, source_run_id, observed_at).
  `verification.record_observation(..., source_run_id=)` is idempotent (returns the existing row).
For B4: `ingest_reward` / pipeline.reward / pipeline.verify now lock the experiment row FOR UPDATE (exactly-once); keep that when editing.
