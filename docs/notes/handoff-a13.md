# A13 handoff (Test / Reliability)

## How to run
```
cd backend && .venv/bin/python -m pytest -q                      # whole backend suite (~2 min; Postgres required, sqlite fallback)
cd backend && .venv/bin/python -m pytest -q tests/integration/test_full_loop.py   # the 20-step first complete loop (~15 s)
cd backend && AEO_TEST_DB=sqlite .venv/bin/python -m pytest -q tests/unit        # no Postgres needed
cd backend && .venv/bin/ruff check . && .venv/bin/ruff check tests              # lint (tests/ruff.toml extends pyproject, relaxes a few test-only rules)
cd frontend && pnpm test                                                         # vitest (26 tests)
AEO_SLOW=1 .venv/bin/python -m pytest -q tests/api/test_sse.py                   # also waits for the real 15 s SSE ping
```

## Test DB isolation (important for every agent)
`backend/tests/conftest.py` creates one Postgres database per pytest process, `aeo_test_<pid>`, builds the schema with
`Base.metadata.create_all` (all of `app.models.*` auto-imported), and drops it on exit; DBs of dead pids are reaped. It never runs DROP SCHEMA,
so concurrent runs by several agents cannot collide.
- `AEO_TEST_DB_NAME=aeo_test` pins a shared fixed-name DB (then only our own tables are dropped and recreated).
- `AEO_TEST_DB=sqlite` (or Postgres unreachable) falls back to a per-process aiosqlite file.
- Every table is emptied (DELETE + sequence restart) after each test that used the DB, so tests may commit for real.
- `app.core.db` engine/sessionmaker are pointed at the test DB, so code that opens its own sessions (SSE, workers, pipeline) sees the same data.
- Env is pinned before app import: no Profound/GitHub/LLM credentials, `REDIS_URL` closed port (forces in-process bus / memory cache fallbacks).
- Factories use bare sha256 hex for `content_hash` (matches A6).

## Fixtures and helpers
`tests/conftest.py`: `engine`, `sessionmaker`, `session`, `org`, `clock` (FakeClock), `emit` (EmitRecorder, accepts both emit shapes),
`mock_http` (respx; unmocked outbound HTTP fails loudly), `web_collector`, `app_client` (httpx AsyncClient on the real FastAPI app),
`fastapi_app` (raw ASGI app for SSE), `fast_web` (fake public DNS + no politeness sleeps), `no_queue` (queue unreachable -> pipeline chains inline fast),
`inline_queue` (API jobs run in-process via AEO_QUEUE_INLINE), `set_env` (env + Settings cache refresh).
`tests/factories.py`: org, prompt cluster, signal / signal series (`visibility_drop_values`), incident, evidence, hypothesis, intervention, approval,
experiment, policy version. `tests/helpers.py`: FakeClock, EmitRecorder, `asgi_stream` + `parse_sse` (httpx ASGITransport cannot stream SSE), `mutating_calls`.
`tests/support.py`: `seed_world` (RECORDED/TEST ONLY org + signals + web pages) and `mock_github`. Fixture data is labeled `RECORDED/TEST ONLY`.
A deterministic `FixtureRanker` stands in for the EvidenceRanker in the loop test only (the loop is deterministic; the real ranker is covered by A8).

## Inventory (A13-owned files)
| File | Covers |
|---|---|
| `unit/test_no_demo_mode.py` | scans app/ml/frontend source for DEMO_MODE, `/demo` routes/pages, fake-data markers, mockup incident titles, demo settings fields |
| `integration/test_db_schema.py` | create_all from empty DB, expected tables, unique/FK/cascade constraints, PolicyVersion + decided-Approval immutability, enum validation |
| `integration/test_detection_db.py` | signals -> incident (detector + priority), idempotent re-detect, org scoping, noise ignored |
| `integration/test_approval_safety.py` | execution gate (none/pending/rejected/expired/model/system), human-only decisions, immutability, modified diff recorded and used, observe exemption, ledger gating, HTTP 409/403 paths |
| `integration/test_executor_db.py` | `run_execution` requires approval; dry-run = zero HTTP; configured+dry-run = zero mutating HTTP; live (mocked) opens PR, never merges; modified diff committed; failures typed, no false success; pipeline.execute dry-run regression |
| `integration/test_full_loop.py` | 20-step loop through real pipeline/API/executor/ledger/reward/policy versioning; dry-run branch; native gate confirmation and policy-context regressions |
| `integration/test_false_success_hunt.py` | adversarial invariants (reward needs qualifying measured post-intervention data, dry-run never rewarded, gate bypass, unexecutable selection) |
| `degradation/test_crawl_failures.py` | HTTP errors/timeouts -> unavailable/failed evidence with no excerpt/score; partial failure; no domains; SSRF |
| `degradation/test_degraded_modes.py` | Profound unconfigured/5xx, LLM absent/401, all pages failing, ML artifact missing -> labeled fallback + degraded capability, no policy -> cold-start v0.0.1, no observation -> awaiting + reward NULL, redis down -> degraded |
| `api/test_api_core.py` | health, OpenAPI contract paths, capabilities, org create/duplicate, incident list/filters/search/pagination/detail, 404/409, audit on transitions |
| `api/test_sse.py` | ordered replay, `Last-Event-ID` and `after_seq` resume, live delivery, per-incident isolation, no fabricated events, 404, history endpoint, bus heartbeat, no-redis persistence, no duplicates; slow real-ping test behind AEO_SLOW |
| `unit/test_conftest_smoke.py` | harness sanity |
| `frontend/tests/composables.test.ts` | SSE composable (dedupe, order, heartbeat ignored, failed steps kept, close on unmount), approval composable has no optimistic state |
| `frontend/tests/no-fake-data.test.ts` | frontend source scan: no demo flags/routes, no mockup text, no premature success copy, no client-side reward math |
Other agents' unit tests in `tests/unit/` (and A12's `tests/api/test_smoke.py`, A14's `test_pipeline_tolerance.py`) are untouched. (An early `ruff --fix` run touched import order in some of them.)

## Status at handoff
Backend (last full run): 683 passed, 1 skipped (slow real-ping SSE), 5 failed. The 5 failures are the open P1 finding below and are intentionally red until the owner fixes it.
Frontend: 26 passed. Ruff: `ruff check app` has pre-existing findings in other agents' code; `ruff check tests` clean for A13 files.
Open red tests (see `docs/notes/requests-a13.md` item 1): `test_false_success_hunt.py` x5 (`learning.ingest.ingest_reward` rewards pre-window / dry-run observations and does not persist after_metrics).

## Bugs found (details, file:line and fixes in requests-a13.md)
Fixed already during the build (now regression-guarded): pipeline.execute passed the wrong arguments to the executor and ignored the dry-run result;
policy context ignored detector metrics (`key` vs `metric`) so every incident selected `observe`; hypotheses never cited Profound metric evidence so the
evidence gate could never confirm; `Experiment.number` MissingGreenlet; state machine confirmed without a gate; executor accepted unknown actor type.
Also fixed during the build: policy selecting an action with no executable change. Open: ingest_reward false-success (P1), "human" is only the X-Actor header (P2), forced verification provenance (P3).

## Scope notes
- The 20-step loop is split in two runs of the same pipeline: the live-mocked-GitHub run (steps 1-20) and a dry-run run (no credentials) that proves nothing
  mutates and the experiment can never be verified or rewarded; a dry-run experiment cannot reach "pending verification" by design (A5/A14 rule).
- Not covered: real Profound, real LLM, real GitHub, browser/E2E (A11 smoke-tested manually), OBP validation (A9 `a9-obp-validation.json`).
