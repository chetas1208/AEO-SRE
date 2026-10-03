# Integration log (A14)

Gate status is updated as code lands. Latest entry first.

## Conventions decided by the lead
- Ruff: `[tool.ruff.lint] select = E,F,I,UP,B` (E501 off; line length 110 is advisory). Owners fix their own files.
- Alembic: one `0001_initial_schema` revision regenerated from metadata until first release
  (`scripts/regen_initial_migration.sh`); sequences (`incident_number_seq`, `incident_event_seq`,
  `experiment_number_seq`) are injected by `migrations/env.py` because autogenerate omits them.
- Job queue: `app.core.queue.enqueue(kind, payload)` (A12 facade) -> `app.workers.queue.enqueue_job` -> Job row + arq.
  Handlers live in `app.workers.jobs.HANDLERS` and call `app.services.pipeline`.
- Reward path: `learning.ingest_reward` is the ONLY writer of Reward + PolicyVersion. `experiments.verification.evaluate`
  (which also writes Reward) is not used by the pipeline.
- Dry-run executions never verify/reward (A5 state machine). The full reward loop can be demonstrated truthfully with
  `observe` (real, no mutation) or a real GitHub PR.

## Gates (latest run)
| Gate | Status | Notes |
|---|---|---|
| alembic upgrade head from empty DB | pass | scratch DBs `aeo_mig`, `aeo_live`; shared dev DB `aeo` is on an older stamp (drop/recreate it) |
| alembic check | pass | |
| ruff (`make lint`) | pass | |
| pytest | 5 fail / ~690 pass | 5 = A9 `ingest_reward` false-success guards (see lead-requests.md #1) |
| API boot | pass | `/api/health`, `/openapi.json`, 30 paths, capabilities degrade correctly with no keys |
| worker boot | pass | 11 jobs + 3 crons; persisted Job rows, retry/backoff tested |
| frontend typecheck / build / vitest | pass | 13 tests |
| frontend UI vs live API | pass | Playwright (LD_LIBRARY_PATH=~/.local/beatit-libs/root/usr/lib/x86_64-linux-gnu): 4 routes render, 0 console errors |
| contract (frontend calls vs openapi) | pass, types stale | all used `/api/*` paths exist; `api.generated.ts` lacks `/api/events` |
| SSE | pass | 38 events replayed/streamed in lifecycle run |
| live 20-step lifecycle (`scripts/e2e_lifecycle.py`) | **20/20** | observe path, DEV FIXTURE signals, real public web evidence; gate honestly unconfirmed (first crawl has no competitor diff) so policy = rule_fallback observe |
| dry-run path (manual) | pass | human override of selection -> manual-override experiment, dry-run executed, verify refused (409) |
