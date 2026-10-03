# Handoff B5 (reliability, adversarial testing, integration)

Living document. B5 is the only committer. Implementers: read "Contracts you can rely on" before touching API-visible behaviour.

## Contracts you can rely on (already in the tree)

- **Domain errors** (`app/domain/errors.py`): `DomainError(message, details)` with `.code`, `.http_status`. Subclasses:
  `InvalidStateTransition`, `InsufficientEvidence`, `ExperimentNotVerifiable` (`.too_early(eligible_at)` -> code
  `EXPERIMENT_NOT_VERIFIABLE_YET`, `details.eligible_at`), `ProviderNotConfigured`, `ProviderUnavailable`,
  `InvalidEvidenceReference`, `ActionNotEligible`, `DuplicateReward`. Raise them from domain code; `app/api/errors.py`
  maps them centrally (also maps legacy `IllegalTransition`, `IllegalExperimentTransition`, `NotRewardable`, `AlreadyRewarded`,
  `ExperimentCollision`, `SpecError`, `ProfoundError`, `LLMError`, SQLAlchemy `OperationalError`/`InterfaceError`).
  Body: `{"error": {"code","type","message","details","request_id"}}` (`type` is the legacy lowercase alias). No stack traces.
- **Mutation endpoints are retry-safe**: approve/reject repeated with the same decision returns the recorded outcome
  (activation is re-run idempotently if it had failed); a recorded decision is final (approve after reject -> 409
  `INVALID_STATE_TRANSITION`). Approval + ledger link are one transaction: if `ledger.attach_approval` raises, NOTHING is recorded.
  Concurrent mutations on one intervention/experiment are serialized by a Postgres advisory lock (`app/api/locks.py`).
  Operator-masked action -> 409 `ACTION_NOT_ELIGIBLE` at approval. `POST /api/experiments/{id}/verify` before the window
  opens -> 409 `EXPERIMENT_NOT_VERIFIABLE_YET` (`details.eligible_at`), `force` never relaxes it; a repeat returns the in-flight job.
- **Pagination**: incidents / experiments (`limit` 1..200) and `/incidents/{id}/evidence` (`limit` 1..1000, default 200, `offset`).
- **Body limit** 1 MiB (413 `PAYLOAD_TOO_LARGE`).
- **SSE**: every event keeps its internal `stage` and gains `event_type` from `app/core/event_types.py` (small fixed vocabulary:
  investigation.started, provider.request.started/completed, hypothesis.proposed/confirmed, policy.completed, approval.requested,
  experiment.activated, verification.scheduled/completed, reward.created). Unknown stages keep their own name. Metadata > 4 KiB is
  replaced by a marker on the stream (full row stays in the DB / `/events/history`). Nothing is synthesized.
- **Health** (`GET /api/health`): `database`, `redis`, `profound_state` (NOT_CONFIGURED|UNVERIFIED|READY|DEGRADED, passive),
  `model_state` (NOT_CONFIGURED|READY|AUTH_FAILED|DEGRADED, passive), `git_sha`. 200 while the DB is up (redis down -> `degraded`),
  503 only when the DB is down. Unconfigured Profound/model never degrade overall.
- **Logging**: structlog with `request_id`, `incident_id`/`experiment_id`/`intervention_id`/`organization_id` (from the URL), worker
  `job_id`/`job_kind`/`attempt`, `duration_ms`, `status`. A redaction processor scrubs configured credentials and Authorization-like keys.
- **Worker**: orphaned `running` jobs older than 30 min are reaped to `failed` ("interrupted") by `cron_reap` (every 30 min + on start).
- **Eval**: `make eval` now also scores `invalid_action_mask`, `observe_required`, `false_reward`; report has `false_reward_failures` and
  `release_blocked`; the CLI exits 2 if unsupported confirmations, temporal leakage or false reward is non-zero.

## Adversarial suites (`backend/tests/reliability/test_adv_*.py`)
`api` (verify/approve/mask/idempotency/pagination/413/no-leak/DB outage), `concurrency` (Postgres: parallel approve, approve-vs-reject,
reward ingestion, verify jobs, detect), `failures` (rollback mid-approval, activation retry, worker redelivery/reap, Redis loss, DB loss in
job), `profound` (12 hostile upstream behaviours, partial, duplicate, no fixture fallback), `fetch` (47: internal IPs, schemes, redirects,
rebinding, bombs, hostile HTML), `model` (14 hostile model behaviours through the real pipeline), `observability`, `perf` (N+1 guards).

## xfail / open items (updated as work lands)
None. No xfail was needed. Known gaps: DNS-rebinding TOCTOU in the fetcher (needs an IP-pinning transport, B2 note); guards are ORM-level (raw SQL bypasses them, B1 note); inconclusive experiments stay `verified`.

## Final status
Full suite 1456 passed / 1 skipped, eval 28/28, alembic head 0002, audit-db clean, fresh-DB upgrade clean, frontend typecheck/Vitest 34/build pass.
