# AEO SRE — Progress

## Current State (2026-10-03, docs truth pass D2)

Layers are kept distinct: IMPLEMENTED (code in tree), TEST VERIFIED (automated), LIVE VERIFIED (real external system), BLOCKED EXTERNALLY. Older sections below are dated history; where they conflict with this section, this section wins.

### Test and check numbers (source and date; nothing here was re-run by this pass unless stated)

| Check | Result | Source |
|---|---|---|
| Full backend suite | 1466 passed, 1 skipped | last confirmed at commit 464bf75 (2026-10-03) |
| Full backend suite, later | 1475 passed | reported by P1 (`docs/notes/handoff-p1.md`); reported, not re-run |
| Frontend Vitest | 48 tests | reported, not re-run |
| `make eval` (decision regression) | 28/28, unsupported confirmations 0, temporal leakage 0, false reward 0 | 2026-10-03 run recorded in README/EVALUATION; not re-run |
| Change Guard G2 claims corpus | 39 passed (`tests/changeguard_claims`) | `docs/notes/handoff-g2.md`; reported, not isolated in this pass |
| `make eval-guard` | 22/24 scenarios passed, **release_blocked = True**: gates `false_allow` (1 violation: target variants such as uppercase host/path, `www.` prefix, percent-encoded path returned ALLOW instead of DELAY) and `digest_binding` (1 violation: scenario `approval_bound_to_digest`) | `docs/GUARD_EVALUATION.md` / `experiments/guard_evals/20261003T205039Z/report.json`, generated 2026-10-03T20:50Z by a run not made by this pass; gates `delay_without_eta` and `silent_pass` were 0 |
| Guard suites (`tests/changeguard`, `tests/changeguard_claims`, `tests/guard_reliability`) | 190 passed, 10 failed, 1 skipped (3m25s) | run by this pass on 2026-10-03 against a working tree with uncommitted edits by other agents; failures include `test_target_variant_is_still_delayed[...]` (5 variants), `test_expired_claim_does_not_block` and `test_edit_after_approval_blocks_manual_execution_409` (3 further failures were not captured in the truncated output) |
| Alembic | `heads` = `current` = `0004` (live dev database) | `alembic heads` / `alembic current`, 2026-10-03 |

### Live and verified

- **Profound: LIVE VERIFIED** via a real key. Account "Chetas Nikunjbhai Parekh (Hackathon)". 7 surfaces `live_verified` (visibility, citations, prompts, prompt_volume, factcheck, query_fanouts, agents). The competitors surface is degraded/unavailable because the account tracks no competitor assets (capability detail `no_competitor_assets_tracked`); competitor domains on the org are operator-supplied, not from Profound, so there is no Profound competitor-share series.
- **Model gateway: LIVE VERIFIED** with protocol `anthropic_messages`; fast tier `claude-haiku-4-5`, deep tier `claude-sonnet-4-6`. `/api/health` at 2026-10-03T20:49Z: database, redis healthy; `profound_state=READY`, `model_state=READY`.
- **Real organization Mixpanel / `mixpanel.com`**: 488 LIVE signals (255 visibility, 100 query fanout, 90 citation, 43 prompt volume), all with `raw_payload_ref`. Incidents #2-#7 (visibility drops and a prompt-volume spike) were all investigated live and stopped at `awaiting_approval` with `observe` policy decisions (`rule_fallback`, cold start). #2 and #3 used rule-fallback hypotheses (investigated before the model was available); #4-#7 used the live model. Nothing has been approved or executed. Source: `docs/notes/handoff-p1.md`. Brand choice (Mixpanel vs the other categories) still needs user confirmation.
- **Fixture Experiment 1** (EXP-0001, auth0.com fixture org): `awaiting_verification`, no after-metrics, no reward; verification window opens **2026-10-04T20:29:43Z**. It is read-only for all new work.

### Mocked or fixture only

- GitHub PR executor: HTTP flow tested against mocks, never run against real GitHub (optional).
- Change Guard demonstrations use SIMULATED agents only (`scripts/simulate_agent_change.py`).
- Fixture org auth0.com has no Profound asset; its Profound path is unavailable by design (never live).
- The synthetic corpora (`tests/changeguard_claims/corpus.jsonl`, `make eval` scenarios) are hand-built, not production data.

### Blocked

- **Verification window time**: no reward before 2026-10-04T20:29:43Z; early verification is correctly rejected (409).
- **Profound Agent -> Call API -> `POST /api/change-checks` reachability**: Profound runs in its cloud; the local API needs a tunnel or deployment. Not set up. No real Profound Agent has called the endpoint.
- `ProfoundAgentExecutor` is an unavailable stub; a CMS executor does not exist.
- No competitor assets tracked in the Profound account (no competitor series).
- Human approval of real incidents #2-#7 (needs the user).

### Change Guard (feature, DEC-041)

Implemented by agents G1-G4 and committed in `ac84d54` (2026-10-03): `backend/app/changeguard/` (claims, contradiction, decision, digest, overlap, semantic, service, targets, canonical), routes `change_checks.py` and `canonical_claims.py` mounted in `app/api/main.py`, migration `0004_change_guard.py`, frontend `components/guard/` and `CanonicalClaimsEditor.vue`, tests under `backend/tests/changeguard`, `changeguard_claims`, `guard_reliability`, `backend/evals_guard`, e2e `frontend/e2e/guard.spec.ts`. Three checks (experiment contamination -> DELAY, duplicate/conflicting changes -> MERGE/REQUIRE_REVIEW, canonical-truth conflict -> BLOCK), action-digest approval binding, canonical-truth admin API. Status: IMPLEMENTED; TEST VERIFIED only in part: the 2026-10-03 guard-suite run and `make eval-guard` both show open defects (see the table: target-variant false ALLOW and approval-digest binding), so the release gate is currently BLOCKED; **not LIVE VERIFIED**. Caveat found in this pass: the API process on :8000 reports `git_sha=ae05b01` and its OpenAPI lists no `/api/change-checks` or canonical-claims paths, so the running server predates Change Guard and must be restarted to serve it. Uncommitted edits to `change_checks.py`, `evals_guard/harness.py` and guard tests were in the working tree at the time of this pass.

### Graph and policy (planned)

Neo4j event graph and graph-aware policy are specified in `docs/GRAPH_SPEC.md` and recorded in DEC-043/DEC-044. Infrastructure is IN PROGRESS (agent N1); an uncommitted `backend/app/graph/` (client, capabilities, errors, health) and a `neo4j_state` field on `/api/health` were appearing in the working tree late in this pass, but no projector or outbox was seen, `.env.example` documents optional `NEO4J_*` variables, nothing is projected, and no live Neo4j connection is verified. Postgres stays the system of record.

### Migrations

`0001_initial_schema`, `0002_domain_integrity_constraints`, `0003_signal_source_128` (P1 fix for drift on `signals.source`), `0004_change_guard`. Head is `0004`; dev database is at `0004`.

### Frontend / UI

Nuxt 4 with Tailwind, 3D evidence scene and Change Guard UI (decision chips, protection banner, canonical-truth editor, 3D change topology in `ac84d54`). UI work was split between this Claude session and a Gemini session (DEC-046). hacp coordination: peer "b" never answered the messages sent to it; the wiring contract `c-b4cd...` remains proposed (not accepted). Playwright e2e 9/9 was recorded earlier on 2026-10-03; not re-run in this pass.

### Abandoned directions

- **Incrementality / causal pivot**: partially built by agents, then explicitly dropped by the user. The WIP is backed up outside the repo at `~/aeo-causal-pivot-wip-backup` and is not wired in. Empty directories `backend/tests/causal` and `backend/tests/evals_causal` remain on disk; `backend/app/experiments/causal.py` is the earlier DEC-034 confidence/caveat module, not the pivot. See DEC-042.
- **"Profound Merge" standalone product**: deferred; Change Guard is a feature with a graduation criterion (DEC-041).

### Next actions

1. Fix the open Change Guard gate failures (target-variant normalization false ALLOW; approval digest binding), restart API and worker from the current tree so Change Guard is served, then re-run `make eval-guard` and the guard suites and record the numbers.
2. Provide a reachable URL (tunnel/deploy) and a `CHANGE_GUARD_TOKEN`, then try one real Profound Agent Call API node.
3. Finish the Neo4j infrastructure and shadow-mode policy (DEC-043, DEC-044); verify against the hosted instance before calling it live.
4. Approve or reject real incidents #2-#7; after 2026-10-04T20:29:43Z verify fixture Experiment 1.
5. Confirm the intended brand/category (Mixpanel) and optionally track competitor assets in Profound.

---

## Backend Deep Campaign (2026-10-03)

Verified on the final tree: backend **1456 passed, 1 skipped** (Postgres), ruff clean, `alembic check` clean, `current` = `heads` = `0002`, fresh-database upgrade (21+ tables) clean, `make audit-db` clean, `make eval` **28/28** with unsupported confirmations 0, temporal leakage 0, false reward 0. Frontend: typecheck 0, Vitest 34 passed, build passed, OpenAPI types regenerated from the restarted API.

Changed: database-level integrity (migration 0002), a provider-neutral ModelGateway (contract-tested with fake OpenAI chat, OpenAI responses and Anthropic Messages servers; no live call), Profound provenance and no-fixture-fallback, multi-signal incident correlation, evidence-derived confidence with counterevidence and control checks, a policy eligibility mask, one verification-window service, outcome labelling with OBSERVE semantics, exactly-once reward and policy update, structured API errors, retry-safe approve/reject/verify, advisory locks, pagination, 1 MiB body limit, canonical SSE `event_type`, independent health states with git SHA, redacted structured logging, stale-job reaping.

Adversarial suites (`backend/tests/reliability/test_adv_*.py`) cover early verification, repeated approval, masked actions, concurrent mutations, rollback mid-approval, worker redelivery, Redis and DB loss, hostile Profound upstreams, hostile model output through the real pipeline, SSRF and hostile pages, secret leakage and N+1 queries.

Fixture Experiment 1 is unchanged: `awaiting_verification`, after_metrics empty, no reward, window opens 2026-10-04T20:29:43Z. Profound `NOT_CONFIGURED`, model `NOT_CONFIGURED`. Remaining blockers are external: `PROFOUND_API_KEY`, a model key, the 2026-10-04 window, and a browser runtime.

## Live Profound & Cost-Aware Model Integration (2026-10-03)

- **Live Profound Integration: VERIFIED.**
  - Account: "Chetas Nikunjbhai Parekh (Hackathon)" connected via `PROFOUND_API_KEY`.
  - 488 live `Signal` rows ingested for Mixpanel (`mixpanel.com`): 200 prompts, 255 visibility records, 90 citations, 43 prompt volumes.
  - Capabilities probe: 7/8 surfaces `live_verified` (visibility, citations, prompts, prompt_volume, factcheck, query_fanouts, agents).
  - Detected 2 real live incidents from Profound data:
    - Incident #2: `Product analytics platforms: visibility dropped 87% -> 15%` (`8a69c17c-5092-4fc4-9d5b-dcb64cfd85da`, High priority, visibility drop).
    - Incident #3: `Funnels, retention & behavior analysis: prompt volume up 170% (3157 -> 8511)` (`b6b34bfb-a129-4f73-bd48-e1df06627d7f`, High priority, volume spike).
  - Web evidence: 15 live web pages crawled across `mixpanel.com`, `amplitude.com`, `heap.io`.
  - Real live 35-node, 34-edge 3D evidence DAG generated and rendered in Nuxt 3D scene.

- **Cost-Aware Model Runtime: IMPLEMENTED & VERIFIED.**
  - Centralized two-tier `ModelRouter` inside provider-neutral `ModelGateway`.
  - FAST Tier (Default): `claude-haiku-4-5` ($1/M in, $5/M out) handles routine semantic operations (intent classification, claim extraction, evidence/incident summaries, cluster labeling, intervention drafting, hypothesis generation).
  - DEEP Tier: `claude-sonnet-4-6` ($3/M in, $15/M out) invoked only when deterministic complexity score >= 0.65 or on validation repair escalation.
  - `make model-smoke`: FAST tier tested with tiny intent classification, 850ms latency, $0.000356 cost, PASS.
  - `make eval-model-live`: 100% PASS rate across tasks, confirming 60.4% cost savings with Haiku-first routing.
  - Unit tests: 5/5 in `tests/unit/test_model_router.py`, 25/25 in `tests/unit/test_model_gateway.py`.

- **UI & Browser Verification: 9/9 PASS.**
  - Headless Chromium with local `libasound2t64` in `~/.local/lib`.
  - Playwright visual proof: 9/9 passing tests with full-page screenshots in `frontend/test-artifacts/screenshots/` showing emerald `LIVE PROFOUND` badge and 3D evidence DAG.

- **Originality & Provenance Audit: COMPLETE.**
  - Confirmed AEO SRE is our own implementation developed specifically for AI discovery.
  - Prior open-source/hackathon systems provided research and architectural inspiration; no source code was copied or imported from `references/`. `references/` remains gitignored and isolated from production code. All legal third-party notices, datasets (FEVER, VitaminC), and Profound attribution remain intact.

## Working Now

- `GET /api/health` 200 at 2026-10-02T19:28:42Z.
- `alembic current` → `0001 (head)`. `alembic check` → no new upgrade operations.
- Empty database `aeo_fresh_prove`: `upgrade head` created 21 tables including `policy_decisions` and `settings`, `source_experiment_id` on `policy_versions`, then the database was dropped.
- Executors: `manual` healthy, `github` unavailable (optional), `profound_agent` unavailable stub.
- Public Profound OpenAPI at `https://api.tryprofound.com/openapi.json` version `d8b349a8a87aca98bad1c21c1c222e6a11497b41`. Paths `/v2/reports/visibility`, `/v2/reports/citations`, `/v2/reports/query-fanouts`, and `/v1/org/categories` exist. No authenticated call was made.
- OpenAPI types regenerated from `app.openapi()`; `frontend/types/api.generated.ts` did not change.

## Completed

Integrated and covered by the passing Postgres suite:

- Domain objects, state machine, audit, Alembic `0001`.
- Detection, priority, evidence graph, evidence gate, rule RCA.
- EvidenceRanker artifact and inference path, with heuristic fallback.
- LinUCB / Thompson, immutable policy versions, `observe`.
- Approval, then `ManualExecutor` activation without GitHub.
- Verification stops at `verified`. Reward and the next policy version are written only by `ingest_reward`.
- Frontend routes for incidents, experiments, and settings.

## In Progress

Milestone 10. The loop is proven on fixtures, including one labeled dev-fixture run on the local database. It has not been proven on a real Profound account.

A `dev_fixture` baseline for auth0.com was seeded, detected, and investigated. The policy selected `observe`. The evidence gate left the hypothesis proposed. Metric evidence from that run is labeled `dev_fixture`, not Profound.

## Blocked

### Blocker: Profound credentials absent

Impact: No live visibility, citation, competitor, or prompt-volume signal. Milestone 3 cannot be live-validated. Milestone 10 cannot show a real incident or an honest "no incident" from Profound data.

Evidence: capabilities `profound.state=unavailable`, detail `PROFOUND_API_KEY not configured`. The key in `.env` is empty.

Workaround: Connector tests use synthetic fixtures. Detection, policy, approval, and manual execution tests do not need the key.

Needs: `PROFOUND_API_KEY` in the root `.env`, then one non-destructive ingest.

### Blocker: Model provider key absent

Impact: Hypotheses and patch text stay on rules and templates.

Evidence: `llm.state=unavailable`, `MODEL_API_KEY` empty.

Workaround: Rule RCA. Tests mock HTTP.

Needs: `MODEL_API_KEY` only if a live structured hypothesis is required.

## Broken / Known Failures

- No live Profound or model request has succeeded. Do not describe those integrations as live.
- First crawl of a page has no diff, so the RCA rules often emit no actionable hypothesis and the policy falls back to `observe`. That is intended. The fixture run did this.
- The trained ranker scores an incident headline as a claim and usually returns `insufficient`. The gate is not weakened to hide that.
- Freshness head AUC is 0.502 and is not used.
- Dedup is not a unique database constraint. Two concurrent detects for one org could both insert.
- Docker Compose was not executed in this pass. Postgres and Redis were already up.
- The IDE browser cannot reach this machine's localhost, and local Chromium/Firefox fail to start because `libasound.so.2` is missing. SSE was checked with curl: 38 events replayed in order, and `Last-Event-ID` of the last event returned nothing further.
- PyTorch warns that the NVIDIA driver is older than this CUDA build. Tests still passed; the ranker loaded on CPU.
- Okta's sitemap timed out during the fixture investigation. Auth0 pages were collected.

## Tests

| Test Area | Command | Result | Last Run |
|---|---|---|---|
| Backend Postgres suite | `cd backend && .venv/bin/python -m pytest -q` | 797 passed, 1 skipped, exit 0 | 2026-10-02T19:26:47Z |
| SQLite subset (full loop, profound client, schema, reliability, naive datetimes) | `AEO_TEST_DB=sqlite pytest` those paths | 144 passed, 1 skipped (Postgres-only schema test) | 2026-10-02T19:27:57Z |
| Ruff | `cd backend && .venv/bin/ruff check .` | PASS | 2026-10-02T19:23Z |
| Alembic empty DB | `alembic upgrade head` on `aeo_fresh_prove` | PASS, revision `0001`, dropped | 2026-10-02T19:23Z |
| Alembic check | `cd backend && .venv/bin/alembic check` | No new upgrade operations | 2026-10-02T19:23Z |
| Alembic current | `alembic current` | `0001 (head)` | 2026-10-02T19:23Z |
| Frontend typecheck | `cd frontend && pnpm typecheck` | PASS, exit 0 | 2026-10-02T19:28Z |
| Metric provenance | `pytest tests/unit/test_metric_provenance.py` | 2 passed | 2026-10-02T19:37Z |
| Frontend unit | `cd frontend && pnpm test` | 4 files, 34 passed | 2026-10-02T19:38Z |
| Nuxt build | `cd frontend && pnpm build` | PASS | 2026-10-02T19:28Z |
| Live Profound call | — | BLOCKED, key unset | — |
| Live model call | — | BLOCKED, key unset | — |
| Live GitHub PR | — | NOT RUN; optional and unconfigured | — |

## Frontend Status

Nuxt 4. Routes: `/` → `/incidents`, incident detail, experiments, settings. Types match the current OpenAPI document (regeneration was a no-op). Typecheck, unit tests, and production build passed. No browser pass this session.

## Backend Status

FastAPI `app.api.main:app` on port 8000, started 2026-10-02T19:28:12Z from this working tree. Manual executor is default. `evaluate` persists `after_metrics` and moves to `verified`. It does not write `Reward`.

## Database Status

Live database is at Alembic `0001`, which matches model metadata (`alembic check` clean). A fresh database migrates from zero to that head. `policy_decisions`, `settings`, and `policy_versions.source_experiment_id` are in the migration.

## Profound Integration Status

IMPLEMENTED and MOCK-TESTED. LIVE BLOCKED. Empty key. Public spec version confirmed; no authenticated request.

## ML Status

Artifact `backend/ml/artifacts/evidence_ranker/` version `evidence-ranker-20261002-e9e4fb7c`. Held-out class-balanced test n=12000: accuracy 0.6195, macro-F1 0.617. Freshness head not usable. API reports the version.

## RL / Policy Status

LinUCB and Thompson exist. Live capability is cold-start. No reward row has updated a policy version in the dev database.

## Execution / GitHub Status

`ManualExecutor` is required and healthy. GitHub is an optional adapter, currently unavailable, and is not required in `.env`. `ProfoundAgentExecutor` is a stub that refuses execution. See DEC-030.

## Reference Repository Status

Clones remain in gitignored `references/`. SHAs re-read 2026-10-02. No production import of these trees.

| Repository | Cloned | License Checked | Purpose | Reused Directly | Notes |
|---|---|---|---|---|---|
| BlackBox `b72a32c` | yes | Apache-2.0 | Incident machinery | no | GitHub repair flow was not adopted as the product |
| CampaignPilot `0502103` | yes | MIT | RCA layers | no | |
| Info-Ninja `ee2bfea` | yes | no LICENSE | Web research ideas | no | |
| LaunchPilot `9fd85c1` | yes | no LICENSE | Approval shape | no | |
| Lattice `535ffb7` | yes | no LICENSE | Provenance idea | no | |
| Recoil AI `a65f14f` | yes | no license chosen | Structured analysis | no | |
| SLAP `89eb5b3` | yes | HackUPC-only | Handoff idea | no | |

## Recent Decisions

DEC-030 supersedes DEC-009. Execution is executor-neutral. Manual activation is the core path. GitHub, CMS, and Profound Agent are optional adapters.

DEC-034. Incident detail returns a backend explanation. Causal confidence never reaches `high`. A recorded confounder forces `low`.

## Next Highest-Leverage Tasks

1. Put a real `PROFOUND_API_KEY` in the root `.env` and create one organization whose domain is an owned asset in that Profound account. Ingest runs immediately. Record a real incident, or "no incident under current thresholds."
2. If a model key exists, run one structured hypothesis over persisted evidence and reject unknown evidence ids.
3. Walk `/incidents` in a browser once a local browser can start (`libasound.so.2` is missing here). SSE replay is already confirmed by curl.

Code backlogs that do not need those credentials are in place: rejection categories (not rewards), acceptance rate on the policy endpoint, investigation time and source caps, Profound answer details only during investigation, a Postgres detect lock, and `python -m app.devtools.trace`. See DEC-035.

## Last Verified

Last verified: 2026-10-02T20:27:23Z
Verified by: ruff clean; pytest 817 passed, 1 skipped; alembic check found no new operations; frontend typecheck. Live API: health ok, workers healthy, illegal `awaiting_approval -> closed` returned 409, policy acceptance rate null on 0 decisions.
Git commit: uncommitted. Previous commit remains `67441ea`.

## Change Guard reliability, evaluation and docs (2026-10-03, G4)

- Added `backend/tests/guard_reliability/` (false-ALLOW target variants, canonical/degraded/empty-truth cases, precedence, digest stability, approval-digest binding, idempotency, concurrency, auth/limits/injection, SIMULATED labelling). Written against `docs/CHANGE_GUARD_SPEC.md`; skips until `app.changeguard.service` exists. Not yet run against a finished service.
- Added `backend/evals_guard/` and `make eval-guard` (gates: false ALLOW, DELAY without eligible_after, silent pass, digest binding; exit 2 = release_blocked).
- Added `scripts/simulate_agent_change.py` (SIMULATED agents only) and `docs/CHANGE_GUARD_INTEGRATION.md`, whose Profound claims were checked against docs.tryprofound.com / help.tryprofound.com on 2026-10-03 (URLs in the doc).
- Not verified: any live Profound Agent call; public reachability of the API (needs tunnel/deploy).

