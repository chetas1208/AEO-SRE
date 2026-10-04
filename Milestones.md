# Profound Lift — Milestones

Execution map from the current repository to a working incident-response loop for AI discovery.

Product intent is `Plan.md`. UI contract is `UI.md` (three surfaces: Incidents, Experiments, Settings). Layout in this repo is `/frontend` and `/backend`, which supersedes the `apps/web` / `apps/api` sketch in Plan §5. See `Decisions.md`.

Status values: `NOT STARTED` | `IN PROGRESS` | `BLOCKED` | `COMPLETE`.

A checkbox is marked only when the implementation was verified in the tree or by a command recorded in `Progress.md`. A passing unit test is not a live external integration.

**Honesty layers:** `IMPLEMENTED` (code exists) · `TEST VERIFIED` (automated evidence) · `LIVE VERIFIED` (real external system) · `BLOCKED EXTERNALLY` (credential, time window, browser runtime, host Docker permission). Do not mark `COMPLETE` when only the first two apply.

| Milestone | Implementation | Tests | Live | Blocker |
|---|---|---|---|---|
| 3 Profound ingest | COMPLETE | COMPLETE (mocks + contract) | LIVE VERIFIED (7 surfaces; competitors not tracked by the account) | — |
| 9 Verification/reward | COMPLETE | COMPLETE | WAITING | earliest window **2026-10-04T20:29:43Z** |
| 10 End-to-end | IN PROGRESS | partial | LIVE through approval (incidents #2-#7 at `awaiting_approval`); reward not reached | human approval; verification window |
| 11 Change Guard | IMPLEMENTED (commit `ac84d54`) | PARTIAL: 190 passed / 10 failed in guard suites; `make eval-guard` 22/24, release_blocked | NOT LIVE (SIMULATED only) | API reachability from Profound's cloud; running API predates it |
| 12 Neo4j event graph | IN PROGRESS (infrastructure; early uncommitted files only) | NOT STARTED | NOT VERIFIED | hosted instance not yet exercised by the app |
| 13 Graph-aware policy | NOT STARTED (spec only) | NOT STARTED | — | depends on 11 and 12 |

---

# Milestone 1 — Monorepo Foundation

## Objective

Make the repository runnable and stable enough for parallel work: Nuxt 4, FastAPI, Postgres, Redis, root env, migrations, health, logging.

## Why This Exists

Later milestones share one database, one API, and one frontend. Without this, agents invent competing layouts and env files.

## Deliverables

Root `/frontend`, `/backend`, `.env`, `.env.example`, Compose, Alembic, Makefile, health endpoint.

## Backend

- [x] FastAPI app factory in `backend/app/api/main.py`
- [x] Settings load the root `.env` (`backend/app/core/config.py`)
- [x] Structured logging (`backend/app/api/logging.py`)
- [x] `GET /api/health` reports database and Redis
- [x] Alembic revision `0001` in `backend/migrations/versions/0001_initial_schema.py`
- [x] Live `aeo` database stamped at Alembic `0001` on 2026-10-02. Column set matched a fresh `upgrade head` (0 column diffs) before the stamp.
- [x] `ruff check` clean (2026-10-02, after fixing 38 findings)

## Frontend

- [x] Nuxt 4 app in `frontend/` (`nuxt` ^4.5.2)
- [x] Root `.env` loaded by `frontend/nuxt.config.ts`; only `NUXT_PUBLIC_API_BASE_URL` is public
- [x] Dev server responded HTTP 200 on `http://127.0.0.1:3000/`
- [x] `pnpm typecheck` exited 0 on 2026-10-02
- [x] `pnpm build` run and recorded (2026-10-02, production build passed)

## Data / ML / RL

Not this milestone.

## Integration

- [x] `docker-compose.yml` defines Postgres (pgvector) and Redis, plus optional `app` profile
- [x] Postgres port 5432 and Redis port 6379 accept connections
- [x] `.env.example` matches the root config surface; `.env` is gitignored
- [ ] Docker CLI usable by this user (`docker info` returned a permission error)
- [x] `README.md` and `THIRD_PARTY.md` at repo root

## Tests / Verification

- [x] `cd backend && .venv/bin/python -m pytest -q -rs` — 632 passed, 1 skipped (2026-10-02T09:27:46Z). Skip: 15s SSE ping unless `AEO_SLOW=1`.
- [x] `alembic upgrade head` on a throwaway empty database `aeo_alembic_empty` reached revision `0001` (21 tables including `alembic_version`), then the database was dropped
- [x] Live `aeo` database stamped `0001` after a column-level match against a fresh upgrade

## Exit Criteria

Frontend starts, backend starts, Postgres works, Redis works, migration from an empty database is the path the live DB uses, root env conventions hold, no secrets committed, base tests pass, lint is clean.

## Dependencies

None.

## Risks

Live schema and Alembic can drift if someone uses `create_all` instead of a revision. This account could not run Docker Compose earlier; Postgres and Redis were already listening.

## Status

COMPLETE

Evidence (2026-10-02): health 200 with Postgres and Redis healthy; `alembic upgrade head` on empty database `aeo_fresh_prove` reached `0001` (21 tables) and was dropped; `alembic check` reported no new operations; `alembic current` is `0001 (head)`; ruff clean; frontend typecheck, 34 Vitest tests, and production build passed. `.env` is gitignored. GitHub settings are optional.

---

# Milestone 2 — Core Domain + Incident Lifecycle

## Objective

Persist the domain objects and reject illegal incident transitions.

## Why This Exists

The product is the loop over durable objects, not a chat transcript.

## Deliverables

SQLAlchemy models, Pydantic schemas, state machine, audit events, incident read/create API.

## Backend

- [x] `Organization`, `Signal`, `Incident`, `PromptCluster`, `Job`, `AuditEvent`, `Setting` (`backend/app/models/core.py`)
- [x] `Evidence`, `EvidenceNode`, `EvidenceEdge`, `Hypothesis` (`backend/app/models/evidence.py`)
- [x] `Intervention`, `Approval`, `Execution`, `Experiment`, `Observation`, `Reward` (`backend/app/models/interventions.py`)
- [x] `PolicyVersion`, `PolicyDecision` (`backend/app/models/policy.py`)
- [x] Explicit transition map (`backend/app/incidents/state_machine.py`)
- [x] Incident list/detail/detect/investigate/dismiss routes (`backend/app/api/routes/incidents.py`)
- [x] Audit helper (`backend/app/core/audit.py`)
- [x] Live `aeo` database contains these tables
- [ ] Live database has a non-fixture incident produced by the detector (the only incident is `dev_fixture`)

## Frontend

- [x] Incident queue and detail pages call `/api/incidents`
- [ ] Confirmed in the browser against a real detected incident

## Data / ML / RL

Not this milestone.

## Integration

- [x] Shared enums in `backend/app/domain/enums.py`
- [x] Frontend types under `frontend/types/`

## Tests / Verification

- [x] State-machine and API tests are part of the 631 passing tests
- [x] Illegal transitions exercised against the live API and recorded (`awaiting_approval` → `closed` returned 409 on 2026-10-02)

## Exit Criteria

Entities persist, migrations are the schema source of truth, illegal transitions are rejected, the API can create and read incidents, audit history exists for those actions.

## Dependencies

Milestone 1.

## Risks

Plan experiment states say `awaiting_reward` / `evaluated`. Code uses `awaiting_verification` / `verified` / `rewarded`. Do not invent a second vocabulary. See DEC-026.

## Status

COMPLETE

Evidence: models and revision `0001` include signals, incidents, evidence, hypotheses, interventions, approvals, experiments, observations, rewards, policy versions, policy decisions, settings, and audit events. `source_experiment_id` is on `policy_versions`. The Postgres suite (797 passed) includes illegal-transition and audit coverage.

---

# Milestone 3 — Profound Signal Ingestion

## Objective

Ingest real Profound observations through a connector that normalizes payloads and reports capability status.

## Why This Exists

Profound is the observability substrate. The rest of the loop is useless if signals are invented.

## Deliverables

Authenticated client, capability report, normalizers, raw payload store, `ingest_org`.

## Backend

- [x] Client, request models, errors, retries (`backend/app/connectors/profound/`)
- [x] Normalizers that do not leak Profound row shapes into the domain
- [x] Capability surfaces: visibility, citations, prompts, prompt_volume, competitors, factcheck, agents
- [x] `ingest_org` in `backend/app/services/ingestion.py`
- [x] No key → every surface `unavailable` and zero network (verified by unit tests and by live `GET /api/system/capabilities`)
- [ ] Live authenticated request (key is unset)
- [ ] Recorded redacted payloads from a real account

## Frontend

- [x] Settings / system status can render capability state
- [ ] Last successful ingest timestamp bound to a real sync (live `last_ingestion` is null)

## Data / ML / RL

Raw payloads are meant to land in `data/profound_raw/` (gitignored). None exist from a live call.

## Integration

- [x] Pipeline job kind `ingest_profound_signals` exists
- [x] Worker is running (capabilities `workers=healthy`, queued jobs 0, on 2026-10-02T20:24Z)

## Tests / Verification

- [x] `backend/tests/unit/test_profound_client.py` included in the passing suite
- [x] Fixtures are labeled synthetic except the OpenAPI subset

## Exit Criteria

A real authenticated request succeeds where credentials exist, normalized signals persist, missing capabilities stay explicit, raw provenance is inspectable, connector tests exist.

## Dependencies

Milestone 2.

## Risks

v2 reports are beta. Score scale is unverified. FactCheck depends on category setup. Prompt volume is keyword-level, mapped here to topic names.

## Status

COMPLETE (update 2026-10-03: LIVE VERIFIED, see below; the 2026-10-02 text that follows is retained as history)

Update 2026-10-03: a real `PROFOUND_API_KEY` is configured. 7 surfaces are `live_verified` (visibility, citations, prompts, prompt_volume, factcheck, query_fanouts, agents); 488 LIVE signals ingested for Mixpanel/`mixpanel.com`; re-ingest is idempotent (created 0, updated 0 after a float-tolerance fix). The competitors surface is degraded because the account tracks no competitor assets. Source: `docs/notes/handoff-p1.md`.

History (2026-10-02): Implementation and mocked tests were in the passing suite. Live validation was blocked: `PROFOUND_API_KEY` was empty. The worker ingest path returns `unavailable` and writes no signals. A public spec check on 2026-10-02 found OpenAPI version `d8b349a8a87aca98bad1c21c1c222e6a11497b41` and the visibility, citations, query-fanouts, and categories paths. Query fanouts are in the connector and ingest loop; material shifts become evidence, not separate incidents. That is not an authenticated call.

---

# Milestone 4 — Incident Detection + Prioritization

## Objective

Turn normalized signals into deduplicated incidents with a transparent priority score.

## Why This Exists

A metric change is not automatically an incident. Priority must be explainable and must not invent revenue.

## Deliverables

Rolling baseline, thresholds, grouping, severity, priority components.

## Backend

- [x] Detector (`backend/app/incidents/detector.py`)
- [x] Priority formula components (`backend/app/incidents/priority.py`)
- [x] `POST /api/incidents/detect` enqueues work
- [ ] Detector run against persisted Profound signals (live signals are `dev_fixture` only)
- [x] Dedup proven on the live database, not only unit tests (second detect of the fixture org created 0 incidents on 2026-10-02)

## Frontend

- [x] Queue filters for severity and time range exist in the incidents UI
- [ ] Priority component breakdown verified against API data in the browser

## Data / ML / RL

Priority is a heuristic, not a model. See DEC-013 in `Decisions.md`.

## Integration

Detection is a worker job (`detect_incidents`). Two such jobs are queued and not running.

## Tests / Verification

- [x] `tests/unit/test_detector.py` and `tests/unit/test_priority.py` are in the passing suite

## Exit Criteria

Repeated signals do not spam incidents, meaningful changes create incidents, low-value noise can be ignored, every scoring factor is visible.

## Dependencies

Milestones 2 and 3. Detector unit tests can proceed on fixtures without a live key.

## Risks

Hand-set thresholds. Dedup ignores closed incidents, so a persistent anomaly can reopen. Concurrent detects for one org are not guarded by a unique constraint.

## Status

COMPLETE

Evidence: detector, priority, and pipeline tests in the Postgres suite, including duplicate-signal idempotence. Priority is a scored heuristic with no revenue figure. Not live-validated on Profound data (see Milestone 3).

---

# Milestone 5 — Investigation + Evidence DAG

## Objective

Investigate an incident into persisted evidence, a provenance graph, and structured hypotheses that stay unconfirmed until the evidence gate passes.

## Why This Exists

An LLM sentence is not a root cause.

## Deliverables

Web collector, evidence rows, graph serialization, hypotheses, `confirm` gate.

## Backend

- [x] Public-web collector with SSRF checks, robots, hash, cache (`backend/app/connectors/web/`)
- [x] Investigation collector (`backend/app/investigation/collector.py`)
- [x] Evidence graph (`backend/app/evidence/graph.py`)
- [x] Rule RCA plus optional LLM (`backend/app/investigation/rca.py`)
- [x] Evidence gate (`backend/app/investigation/evidence_gate.py`)
- [x] `GET /api/incidents/{id}/evidence|graph|hypotheses`
- [x] One live incident with persisted evidence and a serialized graph (fixture incident: 14 evidence rows, 17 graph nodes, 16 edges, 0 missing provenance after the graph rebuild)

## Frontend

- [x] Analysis, evidence list, and graph components exist
- [ ] Graph rendered from a non-empty backend graph

## Data / ML / RL

Ranker scores can attach later (Milestone 6). Rules do not require the model.

## Integration

- [x] LLM client degrades when `MODEL_API_KEY` is empty (`backend/app/connectors/llm/`)
- [x] `investigate_incident` job actually drained by a worker (fixture investigation finished; evidence and hypotheses are stored)

## Tests / Verification

- [x] RCA, evidence graph, and web collector tests are in the passing suite
- [x] End-to-end investigate on the live API recorded (fixture incident graph and explanation served by the API)

## Exit Criteria

Investigation runs end to end, evidence persists, the graph is an API object, hypotheses are structured, confirmation requires the gate.

## Dependencies

Milestones 2 and 4. Web collection can run without Profound. Full RCA quality needs signals.

## Risks

No JavaScript rendering. DNS rebinding is only partially mitigated. A first crawl has no page diff, so rules often produce no actionable hypothesis. The trained ranker scores an incident headline poorly as a claim; the gate is not loosened to compensate.

## Status

COMPLETE

Evidence: `tests/integration/test_full_loop.py` passed on Postgres and on SQLite after naive-datetime normalisation in `_features_from`. The evidence gate still rejects unconfirmed hypotheses. Unknown LLM evidence ids are discarded. Live web confirmation of a real brand was not run.

---

# Milestone 6 — EvidenceRanker ML

## Objective

Rank whether a passage supports, contradicts, or is insufficient for a claim, plus a freshness risk.

## Why This Exists

Investigation quality should not depend only on keyword overlap or an LLM.

## Deliverables

Dataset loaders, features, training command, metrics, artifact, inference, heuristic fallback.

## Backend

- [x] Loaders and local FEVER / VitaminC JSONL under `backend/ml/datasets/raw/` (gitignored)
- [x] Feature extraction (`backend/ml/features/`)
- [x] Training entrypoint `backend/ml/training/train_evidence_ranker.py`
- [x] Inference plus heuristic fallback (`backend/ml/inference.py`, `backend/ml/heuristic.py`, `backend/app/evidence/ranker.py`)
- [x] Saved artifact `backend/ml/artifacts/evidence_ranker/` version `evidence-ranker-20261002-e9e4fb7c`, trained 2026-10-02T09:26:09Z. Gitignored.
- [x] Held-out metrics in `metrics.json`: class-balanced FEVER+VitaminC test, n=12000, calibrated accuracy 0.6195, macro-F1 0.617. Freshness head AUC 0.502, so `fresh_usable` is false (threshold 0.65). Train/test claim overlap recorded as 2.

## Frontend

Surface ranker scores on evidence when the API returns them. Do not invent scores in the client.

## Data / ML / RL

- [x] Live capability `ml_ranker` is `healthy` with `artifact_version` `evidence-ranker-20261002-e9e4fb7c` and method `model:lex_cos` (API restarted 2026-10-02T10:09:54Z)
- [x] Training command produced `manifest.json` and `metrics.json` in this environment.

## Integration

Investigation can call the ranker. Fallback must stay labeled when the artifact is missing.

## Tests / Verification

- [x] Feature and ranker unit tests pass
- [x] `test_shipped_artifact_if_present` loaded the on-disk artifact in the 09:27:46Z suite (that run did not skip it).

## Exit Criteria

Reproducible train command, real metrics, saved artifact, inference used by investigation, fallback when the artifact is absent.

## Dependencies

Milestone 5 for integration. Training itself does not need Profound.

## Risks

FEVER/VitaminC are Wikipedia-style claims. Web-page transfer is unvalidated. Freshness head AUC 0.502, so `fresh_usable` is false.

## Status

COMPLETE

Evidence: artifact `evidence-ranker-20261002-e9e4fb7c` on disk; held-out n=12000 accuracy 0.6195, macro-F1 0.617. The restarted API reports that version. `test_real_ranker_is_called_by_pipeline_and_scores_land_on_evidence` ran (torch warning, not a skip). Heuristic fallback remains when the artifact cannot load.

---

# Milestone 7 — Intervention Policy + Contextual Bandit

## Objective

Score the fixed action vocabulary with a versioned contextual bandit. Cold start must be explicit. `observe` can win.

## Why This Exists

The product chooses an intervention. It does not generate unconstrained marketing work.

## Deliverables

Context vector, LinUCB (default) and Thompson sampling, priors, immutable `PolicyVersion`, stored decision.

## Backend

- [x] Action vocabulary in `ActionType` matches the execution prompt (`observe`, `update_existing_page`, `create_faq`, `create_canonical_page`, `create_comparison_content`, `publisher_outreach`, `structured_data`)
- [x] LinUCB and Thompson (`backend/app/policy/bandit.py`)
- [x] Cold-start priors (`backend/app/policy/priors.py`)
- [x] Version store (`backend/app/policy/store.py`), default algorithm `linucb`
- [x] `GET /api/policy` and `/api/policy/versions`
- [x] Live policy capability: `degraded`, detail `cold-start priors only; no persisted policy version`
- [x] A decision persisted on the fixture incident (1 policy version, 1 decision, selected `observe`)

## Frontend

- [x] Action panel is built to show the selected action, alternatives, and policy metadata
- [x] Verified against a stored decision (API explanation recommended `observe` and labeled the other numbers as unconstrained probabilities)

## Data / ML / RL

- [x] Reward ingestion and OPE helpers exist (`backend/app/learning/`)
- [x] No learned update until a measured reward exists (correct: 0 reward rows, policy still cold-start)

## Integration

Policy selection is part of proposal in `backend/app/interventions/propose.py`.

## Tests / Verification

- [x] `tests/unit/test_policy.py` is in the passing suite

## Exit Criteria

Decisions are reproducible, selected action and alternatives are stored, policy versions are immutable, cold start is labeled, `observe` can be chosen.

## Dependencies

Milestones 2 and 5. Can score from incident context before a trained ranker exists.

## Risks

Open Bandit Dataset validates machinery only. Do not claim AEO transfer. No measured AEO reward has updated the live policy.

## Status

COMPLETE

Evidence: LinUCB default and Thompson sampling, immutable `PolicyVersion`, cold-start priors, `observe` in the action set. The live database has one policy version and one decision. No reward has updated it.

---

# Milestone 8 — Intervention Approval + Experiment Activation

## Objective

No external mutation before approval. Policy recommends, the human understands WHY, approves / modifies / rejects; an approved intervention becomes an experiment, the system records the exact action, and verification begins. Execution is an interface: the default ManualExecutor needs no credentials (GitHub PR is an optional adapter).

## Why This Exists

The system proposes remediations. A person authorizes them.

## Deliverables

Proposal, diff preview, approve / reject / modify, experiment activation, intervention package (manual executor), `Mark as executed` recording with deviation capture, execution record. Optional GitHub PR adapter never auto-merges.

## Backend

- [x] Approval service (`backend/app/services/approvals.py`)
- [x] Routes: approve, reject, modify, execute (`backend/app/api/routes/interventions.py`)
- [x] GitHub client and executor (`backend/app/connectors/github/`, `backend/app/interventions/executor.py`)
- [x] `InterventionExecutor` interface; `ManualExecutor` (default) + `record_manual_execution` + `POST /api/interventions/{id}/executed` (A16)
- [x] GitHub reported as an optional executor (`unavailable`, not an alarm); `ProfoundAgentExecutor` unavailable stub
- [ ] (optional) Real branch and PR created with GitHub credentials
- [x] Approval of the fixture incident (`observe` approved through the API on 2026-10-02; incident moved to `awaiting_verification`)

## Frontend

- [x] Approval dialog and action panel exist
- [ ] Approve / Mark as executed clicked against the live API

## Data / ML / RL

Not this milestone.

## Integration

Executor must refuse mutation when approval is missing. Tests cover that path.

## Tests / Verification

- [x] `tests/unit/test_executor.py`, `tests/unit/test_propose.py`, `tests/integration/test_approval_safety.py` are in the passing suite
- [x] Manual execution recorded through the API (`tests/integration/test_manual_execution.py`, `scripts/e2e_lifecycle.py`)

## Exit Criteria

Approve and reject work, unapproved mutation is blocked, an approved action can open a PR when configured, failures stay on the execution record.

## Dependencies

Milestones 2 and 7.

## Risks

A GitHub preview is a dry-run and must not be rewarded. A recorded manual execution is a real execution. See DEC-030.

## Status

COMPLETE

Evidence: `ManualExecutor` is the default and needs no credentials. Live capabilities: manual `healthy`, github `unavailable` (optional), profound_agent `unavailable` stub. Approval and manual-activation tests passed with `GITHUB_*` unset. No browser click-through in this pass.

---

# Milestone 9 — Verification + Reward + Learning

## Objective

Close the loop with before/after observations and a reward computed only from those observations, then update the policy version.

## Why This Exists

An approved pull request is not evidence that AI-discovery behavior improved.

## Deliverables

Experiment ledger, verification delay, reward components, policy update.

## Backend

- [x] Experiment ledger and status (`backend/app/experiments/`)
- [x] Reward formula components (`backend/app/learning/reward.py`)
- [x] `ingest_reward` is the pipeline writer of `Reward` and `PolicyVersion`
- [x] `POST /api/experiments/{id}/verify`
- [x] Verification delay setting default 48 hours
- [x] Experiment row exists. Observations and rewards are still 0, which is correct before the verification window.
- [ ] Policy version created from a measured reward

## Frontend

- [x] Experiments list and detail pages exist
- [x] A row with before metrics, no after metrics, and an unset reward while `awaiting_verification` (experiment 1, window opens 2026-10-04T20:29:43Z)

## Data / ML / RL

- [x] OPE helpers exist and are labeled as machinery checks
- [x] No policy update until reward components exist (still one policy version, zero rewards)

## Integration

Dry-run executions must not verify or reward. `observe` is the honest no-mutation path.

## Tests / Verification

- [x] `tests/unit/test_experiments.py` and `tests/unit/test_reward.py` are in the passing suite

## Exit Criteria

Provenance is stored, before and after are separate, reward is unset until observations exist, a measured reward can update an immutable policy version.

## Dependencies

Milestones 7 and 8. Post-intervention Profound data also depends on Milestone 3.

## Risks

Profound cadence is daily. Leaving `awaiting_verification` is the correct behavior, not a bug. `evaluate` stops at `verified`; only `ingest_reward` writes the reward and the next policy version.

## Status

COMPLETE

Evidence: verification no longer writes a Reward row. Reliability tests, including false-success cases, passed on Postgres and on the SQLite subset. The live experiment is `awaiting_verification` with before metrics and a null reward. The window opens 2026-10-04T20:29:43Z. No observation has been accepted yet.

---

# Milestone 10 — Integrated Product + Reliability

## Objective

One live control plane: incidents, investigation stream, evidence, policy, approval, experiments, degraded modes, tests, and honest docs.

## Why This Exists

Separate modules are not the product.

## Deliverables

Nuxt wired to the API, SSE, worker, README, third-party ledger, full checks.

## Backend

- [x] API surface for health, incidents, evidence, graph, hypotheses, interventions, experiments, policy, settings, capabilities, SSE events
- [x] Worker process heartbeating (`workers=healthy` on 2026-10-02T20:24Z)
- [ ] One incident walked from signal through reward on the live stack

## Frontend

- [x] Routes: `/` redirects to `/incidents`, `/incidents/[id]`, `/experiments`, `/experiments/[id]`, `/settings`
- [x] SSE composable `frontend/composables/useIncidentEvents.ts`
- [x] Loading, empty, and error components exist
- [x] Typecheck and production build recorded (2026-10-02)
- [ ] Browser pass of list, detail, approval, and experiments against the live API

## Data / ML / RL

Degraded modes from the live capability report: Profound unavailable, LLM unavailable, GitHub dry-run, policy cold-start, workers healthy. Ranker artifact version is reported.

## Integration

- [x] `scripts/dev.sh` and Makefile targets exist
- [x] `README.md` states the loop and the scientific limits
- [x] `THIRD_PARTY.md` consolidates license notes
- [x] Queued jobs drain or fail visibly (queued jobs 0 while the worker heartbeat is healthy)

## Tests / Verification

- [x] Backend pytest 632 passed, 1 skipped
- [x] Ruff clean
- [x] Frontend typecheck (`pnpm typecheck`, exit 0, 2026-10-02)
- [x] Frontend unit tests (34 Vitest tests, 2026-10-02)
- [ ] SSE consumed by the browser
- [x] Ruff clean (2026-10-02)

## Exit Criteria

The loop in the build brief runs on the live stack without fabricated Profound data, citations, rewards, or demo incidents.

## Dependencies

Milestones 1–9.

## Risks

Do not polish the UI ahead of a real Profound signal. A green Milestone 10 requires a live signal or an explicit, honest "no incident" from real data, plus a browser pass of SSE.

## Status

IN PROGRESS

Proven: the fixture loop runs on the live API from signals through an approved `observe` experiment that is waiting for its measurement window. Workers are healthy. The evidence graph for that incident has no missing provenance.

Update 2026-10-03: real Profound data is now LIVE VERIFIED (488 signals, Mixpanel), live model calls succeeded (`anthropic_messages`, Haiku 4.5 fast tier / Sonnet 4.6 deep tier), and real incidents #2-#7 were investigated live and stopped at `awaiting_approval` with `observe` decisions. Playwright browser smoke 9/9 was recorded earlier on 2026-10-03. Still not proven: a real incident carried through human approval, execution, verification and reward (needs the user and the window); SSE consumed by a browser is not separately recorded. A reward before 2026-10-04T20:29:43Z would be early and must be rejected.

---

# Milestone 11 — Change Guard (experiment protection + cross-agent change checks)

## Objective

Let Profound Agents post intended changes to Profound Lift and get one decision before publishing, protecting running experiments and organisation-approved facts. Feature inside Profound Lift (DEC-041), not a pivot. Spec: `docs/CHANGE_GUARD_SPEC.md`.

## Deliverables

- [x] IMPLEMENTED: guard service, three checks, digest, `POST/GET /api/change-checks`, canonical-claims API, migration `0004_change_guard` (G1/G2; commit `ac84d54`). Not served by the API process running on 2026-10-03 (`git_sha=ae05b01`); restart needed
- [x] IMPLEMENTED: UI components under `frontend/components/guard/` and `CanonicalClaimsEditor.vue`, `frontend/e2e/guard.spec.ts` (G3; commit `ac84d54`). Browser behaviour against a live Change Guard API is not recorded
- [x] Reliability suite: `backend/tests/guard_reliability/` (service exists, so it runs). 2026-10-03 D2 run of all three guard test directories: 190 passed, 10 failed, 1 skipped, with other agents' edits uncommitted in the tree
- [x] IMPLEMENTED: deterministic eval `make eval-guard` (`backend/evals_guard/`). Run at 2026-10-03T20:50Z: 22/24 passed, release_blocked (false_allow 1, digest_binding 1)
- [x] SIMULATED agent script `scripts/simulate_agent_change.py`
- [x] Integration doc written from Profound's public docs: `docs/CHANGE_GUARD_INTEGRATION.md`

## Exit Criteria

Eval gates are zero (false ALLOW on seeded cases, DELAY without `eligible_after`, silent pass, digest binding) and the fixture experiment's target yields DELAY until its window end. Honest limit: demonstrated only with SIMULATED agents.

## Risks

No real Profound Agent has called the endpoint. The API is local, so Profound's cloud cannot reach it without a tunnel or deployment (not done). Canonical truth is only as good as what humans enter. The eval is a scenario regression on hand-built cases, not a population false-positive rate.

## Status

IN PROGRESS. Layers at 2026-10-03: IMPLEMENTED yes; TEST VERIFIED partial and currently failing its own release gate (see the two deliverables above; G2 claims suite 39 passed per `docs/notes/handoff-g2.md`); LIVE VERIFIED no (SIMULATED agents only, no real Profound Agent call); BLOCKED EXTERNALLY: endpoint reachability from Profound's cloud (tunnel/deploy) and `CHANGE_GUARD_TOKEN`. Graduation to a standalone product only under the DEC-041 criterion.



---

# Milestone 12 — Neo4j Event-to-Outcome Graph

## Objective

Project events, agent runs, ChangeSets, conflicts, decisions, experiments and outcomes into a rebuildable temporal provenance graph in Neo4j, with Postgres as system of record and a Postgres outbox feeding the projection. Spec: `docs/GRAPH_SPEC.md`; decision: DEC-043.

## Deliverables

- [ ] Connection and health (`NOT_CONFIGURED | READY | DEGRADED | AUTH_FAILED`), constraints, `graph_outbox` migration
- [ ] Idempotent projector, replay/rebuild, `make graph-audit`, `make neo4j-smoke`
- [ ] Lineage/context API and graph-backed 3D topology payloads
- [ ] Live validation against the hosted instance (no credentials printed)

## Exit Criteria

Neo4j failure never affects approval, activation, verification or reward; counts reconcile with Postgres; every user-facing graph statement maps to a real node or edge.

## Risks

Projection lag; second datastore; graph features may not improve decisions.

## Status

IN PROGRESS (infrastructure by N1). At 2026-10-03 only early uncommitted files (`backend/app/graph/` client/capabilities/errors/health, `neo4j_state` on `/api/health`) were seen; no projector, outbox or graph Makefile targets were seen; `.env.example` documents optional `NEO4J_*` variables. Nothing is projected; no live Neo4j connection from the app is verified.

---

# Milestone 13 — Graph-Aware Control Policy (shadow first)

## Objective

Keep the deterministic safety mask and `BaselinePolicy` authoritative; run `GraphLinUCB` (`graph_context_v1`) in shadow and evaluate it offline. Spec: `docs/GRAPH_SPEC.md`; decision: DEC-044.

## Deliverables

- [ ] Graph feature extraction and persistence with policy decisions
- [ ] `GraphLinUCB` shadow policy and `make policy-eval` (baseline vs LinUCB vs GraphLinUCB vs LinTS, with graph ablation)
- [ ] Graduation criteria check (samples, zero mask violations, override rate)

## Status

NOT STARTED (specification only). Depends on Milestones 11 and 12. Any synthetic evaluation data must be labelled as such.
