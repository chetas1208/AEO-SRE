# Profound Lift

**Profound Lift is an adaptive incident-response system for AI discovery.**

Modern marketing teams can measure how brands appear across AI answer engines, but every visibility loss, citation
shift, factual error and competitive displacement still creates the same questions: Does this matter? What caused it?
What should we do? And did our intervention work?

Profound Lift converts those signals into evidence-backed incidents, investigates their root causes, selects safe
interventions using a learning policy, executes only after human approval, and measures the resulting outcome. Every
intervention becomes an experiment. Every experiment improves the next decision.

Think Sentry / PagerDuty for AI answer engines ("Marketing SRE").

## Why AI-discovery incidents are hard

- Signals are not decisions: a visibility drop says nothing about cause, importance or remedy.
- Causes live in different places: the engine, which sources it cites, our own pages, a competitor's new page, a stale
  third-party article. No single dashboard holds all of them.
- Data is slow and noisy: Profound tracked prompts refresh daily, new prompts take 24-48 h, models change underneath.
  Outcomes are delayed, observational and confounded.
- Doing nothing is often right. A system that always acts is a content generator, not an SRE.

## The loop

```
Profound signals + public web
  -> ingest -> incident detector (rolling baseline, no LLM) -> priority (7 visible components)
  -> investigation: evidence -> ranker -> graph -> hypotheses -> evidence gate
  -> intervention policy (contextual bandit, cold-start priors, versioned) -> candidate actions incl. observe
  -> HUMAN approve / reject / modify -> experiment activation -> executor (Manual by default: package -> human records it)
  -> experiment ledger -> verification window -> measured reward -> new policy version -> next incident
```

```
Profound  ->  AI-discovery observability  ->  Profound Lift  ->  incident response + experimentation + learning
```

Details: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## What the system does

- **Live 3D Control Plane (`/`).** The homepage serves as an operational Flight Deck connecting personal agent intent (Muse), verified product truth, Profound AI-search intelligence, campaign unit economics, and Change Guard-protected experiments into one live control plane. Built with Three.js (`AgentControlGraphScene.vue`), it features spatial depth stratification (Agents z=-10, Campaigns z=0, Decisions z=+5, Experiments z=+10, Outcomes z=+15), real-time SSE streaming (`/api/control-plane/events`), semantic color coding (emerald positive return, rose negative issue, amber pending review, sky blue running), raycast hover tooltips, and an interactive DOM entity inspector.
- **Real Experiment Creation Engine.** Experiment creation is a complete frontend-to-backend workflow (`POST /api/experiments`), accessible directly from `/`, from Discovery Gaps (`/discovery-gaps`), and from Campaigns (`/campaigns`). The stepped creation drawer (`ExperimentCreateDrawer.vue`) enforces domain invariants: mandatory causal hypotheses, frozen baseline metrics from live backend telemetry, temporal verification window scheduling (24h to 14 days), and automatic Change Guard target-protection registration.
- **Change Guard experiment protection.** Change Guard coordinates concurrent changes across autonomous agents and human editors. It blocks conflicting modifications (`409 Conflict`) on protected target URLs or active prompt clusters while an experiment is awaiting verification, preventing experiment contamination and ensuring causal measurement integrity.
- **Profound's role.** Profound is the observability substrate: visibility, citation share, prompt volume, competitor
  share and FactCheck accuracy are ingested as raw payloads plus normalised `Signal` rows
  (`backend/app/connectors/profound`, `services/ingestion.py`). We do not rebuild what Profound ships; we add
  incident triage, evidence, a decision policy and an outcome ledger on top.
- **Evidence model.** Every evidence row carries source, URL, content hash, excerpt, retrieval method, timestamps and
  scores. The per-incident graph (NetworkX over persisted rows) stores provenance on every edge. A hypothesis is
  `proposed` until a deterministic **evidence gate** confirms it (needs supporting Profound signal with a measured
  change, grounded content evidence, timestamp alignment, written rationale, enough aggregate confidence, no strong
  unresolved contradiction). Failed or unavailable fetches never count as support and are never inferred.
- **EvidenceRanker (small real ML).** MiniLM embeddings + engineered features + LightGBM, trained on FEVER and VitaminC
  subsets (40k train / 12k held-out test): **62.0% accuracy, 0.617 macro-F1** on a balanced 3-class test (support /
  contradiction / insufficient), well above overlap and majority baselines but a triage signal, not a verdict. The
  freshness head was a **negative result** (AUC 0.502) and is disabled; runtime freshness risk is a transparent
  heuristic. Artifact and metrics: `backend/ml/artifacts/evidence_ranker/`.
- **Intervention policy.** LinUCB (Thompson available) over a context vector (15 scalar features plus incident type), fixed action set (`observe`,
  `update_existing_page`, `create_faq`, `create_canonical_page`, `create_comparison_content`, `publisher_outreach`,
  `structured_data`). Cold start uses hand-set priors and is labelled "cold-start" until enough same-type experiments
  exist. Each decision stores its policy version and selection probability, so off-policy evaluation (IPS/SNIPS/DR) is
  possible later. If the root cause is not confirmed the only selectable action is `observe`. Every update writes a new
  immutable `PolicyVersion`.
- **Reward.** `0.35 visibility + 0.30 citation + 0.20 accuracy + 0.15 competitive - action cost - risk penalty`,
  computed only from Profound observations after the verification window; components are stored separately; weights
  are config.
- **Human approval.** Approve / Reject / Modify. Approvals are immutable once decided, record actor type, and only a
  human can authorise execution. Approving **activates an experiment**. Execution is an interface: the default
  **ManualExecutor** (always available, no credentials) issues an *intervention package* (exact change/diff, target,
  steps, evidence, risk, rollback, observation window); a human applies it and records it
  (`POST /api/interventions/{id}/executed`, with an optional `actual_change` if they deviated). That is a real execution
  and starts the verification window. `observe` needs no human step. GitHub PR is an **optional** executor (only when an
  operator selects it and `GITHUB_*` exists); Profound Agent is an unavailable stub; CMS is not built.
- **Experiment ledger.** Every proposed intervention is an `Experiment` with policy version and probability,
  alternatives and scores, evidence snapshot (hashed), before/after metrics, approver, executor reference, window and
  reward. States: proposed -> approved -> executing -> executed -> awaiting_verification -> verified -> rewarded
  (+ rejected, failed). Only a GitHub dry-run *preview* is flagged and can never be verified or rewarded; manual executions are real and are rewarded.
- **Failure-safe modes.** Profound down -> signals unavailable, nothing fabricated. LLM down -> rules-only hypotheses,
  investigation marked incomplete where relevant. Crawl fails -> evidence `unavailable`. No post-intervention data ->
  `awaiting_reward`. Low root-cause confidence -> `observe` / human investigation.

## Change Guard (experiment protection)

Change Guard lets a Profound Agent post an intended change (a structured ChangeSet) to Profound Lift through a Call API node and
get one decision back: ALLOW, MERGE, DELAY, REQUIRE_REVIEW or BLOCK. It exists to coordinate **across runs**: this change versus
other pending changes, versus the organisation's canonical claims, and versus experiments still being measured. It is a
feature of Profound Lift, not a separate product ([DEC-041](Decisions.md)).

```
Profound Agent -> Call API node -> POST /api/change-checks -> Change Guard -> decision -> the Agent continues accordingly
```

- Three checks only: active-experiment contamination (DELAY until the verification window service says the measurement is done),
  duplicate/conflicting changes on one target (MERGE / REQUIRE_REVIEW), and canonical-truth conflict (BLOCK).
- Approvals bind to a digest of the proposal; an edit after approval returns `409 APPROVAL_DIGEST_MISMATCH`.
- When the model or ranker is unavailable the response says `semantic_check: degraded`; empty canonical truth is reported as
  skipped. Neither is ever a silent pass.
- **Status:** demonstrated only with SIMULATED agents (`scripts/simulate_agent_change.py`, labelled `SIMULATED` everywhere). No real
  Profound Agent has called it. Profound runs in its own cloud, so the API needs a tunnel or deployment to be reachable; that is
  not set up. At 2026-10-03T20:50Z `make eval-guard` passed 22/24 scenarios and reported release_blocked (target-variant normalization
  and approval-digest binding were open defects), and the API process then running predated this code; treat the feature as
  IMPLEMENTED, not release-ready. Details: [docs/CHANGE_GUARD_INTEGRATION.md](docs/CHANGE_GUARD_INTEGRATION.md), spec
  [docs/CHANGE_GUARD_SPEC.md](docs/CHANGE_GUARD_SPEC.md), checks `make eval-guard`
  ([docs/GUARD_EVALUATION.md](docs/GUARD_EVALUATION.md)).

## Run it locally

Prerequisites: Docker, Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22 with pnpm.

```bash
cp .env.example .env            # all keys may stay empty; see "Environment"
make install                    # backend venv + frontend deps
make dev                        # docker compose up (Postgres+pgvector, Redis) -> alembic upgrade -> api + worker + web
```

API http://localhost:8000 (OpenAPI `/openapi.json`), web http://localhost:3000. Individual targets: `make infra`,
`make migrate`, `make dev-api`, `make dev-worker`, `make dev-web`, `make test`, `make lint`, `make typecheck`,
`make build-web`, `make train-ranker`, `make eval-guard`. Containerised app (optional): `docker compose --profile app up -d --build`.

Rootless Docker: if `docker ps` fails with a socket error, export
`DOCKER_HOST=unix:///tmp/xdg-$UID/docker.sock` (the path of your user's rootless daemon) before `make infra`.

Create an organisation: `curl -XPOST localhost:8000/api/organizations -H 'content-type: application/json' -d '{"domain":"company.com"}'`.

### Environment (root `.env`, never committed)

| Variable | Purpose | Without it |
|---|---|---|
| `DATABASE_URL`, `REDIS_URL` | Postgres, Redis | defaults to local compose |
| `PROFOUND_API_KEY`, `PROFOUND_BASE_URL` | Profound ingestion | Profound reported `unavailable`; no signals are invented |
| `MODEL_API_KEY`, `MODEL_API_PROTOCOL`, `MODEL_BASE_URL` | model provider credentials | rules and deterministic templates only; investigation reported DEGRADED |
| `MODEL_FAST_NAME` (default `claude-haiku-4-5`) | FAST model tier for cost-effective semantic tasks | defaults to Haiku 4.5 |
| `MODEL_DEEP_NAME` (default `claude-sonnet-4-6`) | DEEP model tier for complex RCA escalation | defaults to Sonnet 4.6 |
| `MODEL_DEFAULT_TIER`, `MODEL_ESCALATION_ENABLED` | routing control (default `fast`, `true`) | defaults to fast with escalation |
| `CHANGE_GUARD_TOKEN` | bearer token for `POST /api/change-checks` | endpoint answers 503 `CHANGE_GUARD_NOT_CONFIGURED` |
| (optional, planned) `NEO4J_URI`, `NEO4J_USERNAME`, `NEO4J_PASSWORD`, `NEO4J_DATABASE` | Neo4j graph projection (infrastructure in progress; Postgres works without it) | graph reported `NOT_CONFIGURED`; nothing else changes |
| (optional) `GITHUB_TOKEN`, `GITHUB_OWNER`, `GITHUB_REPO`, `GITHUB_BASE_BRANCH` | optional GitHub PR executor | nothing: Manual is the default; GitHub is reported `unavailable (optional)`, not degraded |
| `VERIFICATION_DELAY_HOURS` (default 48) | wait after a real execution before measuring | |
| `AUTO_INVESTIGATE` (default true) | queue investigation for detected incidents above the action threshold | manual Investigate |

### Cost-Aware Model Runtime

Profound Lift uses an intelligent, cost-aware two-tier architecture (`ModelRouter`) inside the provider-neutral `ModelGateway`:
- **FAST Tier (Default — Claude Haiku 4.5):** Priced at $1/M input and $5/M output (~1/3 of Sonnet), Haiku handles ~80–95% of semantic calls including prompt intent classification, claim extraction, evidence/incident summarization, prompt cluster labeling, intervention drafting, and baseline hypothesis generation.
- **DEEP Tier (Claude Sonnet 4.6):** Priced at $3/M input and $15/M output, Sonnet is reserved strictly for high-complexity incidents where the deterministic complexity score exceeds `model_complexity_threshold` (0.65) or when FAST-tier structured validation fails after retry.
- **Opus is not used by default.**
- **Deterministic Code Governs Decisions:** Claude proposes candidates, but never confirms root causes (handled exclusively by the deterministic evidence gate), calculates deltas, or computes rewards.
- **Verification:** Run `make model-smoke` to test the FAST tier with a tiny intent classification query. Run `make eval-model-live` to compare FAST and DEEP tiers across representative scenarios.

## Verifying the whole loop

`scripts/e2e_lifecycle.py` drives one incident through all 20 steps against the live stack (API + worker + Redis +
Postgres). Web evidence is collected live from public pages. See [docs/notes/integration-log.md](docs/notes/integration-log.md) for the latest run.

## Operational status (2026-10-03)

| Area | Implemented | Tests | Live | Blocker |
|---|---|---|---|---|
| Core API / domain loop | yes | yes | live stack | — |
| Profound connector | yes | yes (contract) | **LIVE** (488 signals, 7/8 surfaces) | — |
| Incident detection | yes | yes | **LIVE** (Mixpanel visibility drop & spikes) | — |
| Evidence graph | yes | yes | **LIVE** (35-node, 34-edge 3D DAG) | — |
| Model runtime (FAST/DEEP) | yes | yes (unit + smoke) | **LIVE** (Haiku 4.5 default + Sonnet 4.6) | — |
| Approval → experiment | yes | yes | **LIVE** (human approval activates experiment) | — |
| Verification / reward | yes | yes | waiting | window **2026-10-04T20:29:43Z** |
| Nuxt UI / 3D Scene | yes | Vitest (48 reported, not re-run; 34 on 2026-10-02) | **Playwright E2E (9/9 PASS, earlier 2026-10-03)** | — |
| Change Guard | yes | partial: 190 passed / 10 failed in guard suites; `make eval-guard` 22/24, release_blocked (2026-10-03) | not live (SIMULATED agents only) | endpoint reachable from Profound's cloud |
| Neo4j event graph + graph policy | planned / infrastructure in progress ([docs/GRAPH_SPEC.md](docs/GRAPH_SPEC.md), DEC-043/044) | no | no | nothing projected yet |

One-command checks: `make verify` (local deterministic), `make model-smoke` (FAST tier check), `make profound-smoke`, `make ingest-live`,
`make browser-smoke` (Playwright Chromium suite). Runbooks: [docs/PROFOUND_CUTOVER.md](docs/PROFOUND_CUTOVER.md),
[docs/VERIFICATION_RUNBOOK.md](docs/VERIFICATION_RUNBOOK.md), [docs/BROWSER_VERIFICATION.md](docs/BROWSER_VERIFICATION.md).

Docker requires access to the Docker daemon (`docker info`). If the host user lacks permission, run Postgres/Redis
another way and use `make dev-api` / `make dev-worker` / `make dev-web` without Compose.

## Truthful status and limitations

- **Profound is live-verified for ingestion** (488 signals for Mixpanel, 7 of 8 surfaces; the account tracks no competitor assets, so there is no competitor series), and contract tests with synthetic, labelled fixtures remain. Score scales were read from live responses but are not independently validated. Scheduled ingest is four times a day (01:15, 07:15, 13:15, 19:15 UTC) plus an immediate ingest when an organization is created. Query fanouts are ingested on that path and only a material share change becomes investigation evidence; that behavior is unit-tested, not live-verified. 
- **The default executor is manual**: Profound Lift never changes a site itself. The optional GitHub PR executor was never run
  against real GitHub (HTTP flow tested against mocks). The Profound Agent executor is an unavailable stub; the CMS
  executor is not built.
- **The model gateway is live-verified** with `anthropic_messages` (fast tier `claude-haiku-4-5`, deep tier `claude-sonnet-4-6`); real incidents #2-#7 were investigated with it and stopped at `awaiting_approval` with `observe`. It is also contract-tested against fake OpenAI-compatible and Anthropic servers. The model only proposes; deterministic code decides.
- Cold-start priors are hand-set configuration, not learned. The bandit has no real AEO outcome data; learning claims
  are limited to the machinery and logged experiments.
- Open Bandit Dataset/Pipeline validate the LinUCB/OPE implementation only (parity with `obp` on IPS/SNIPS/DR, see
  `docs/notes/a9-obp-validation.json`). **The policy was not trained on ZOZO data and no domain transfer is claimed.**
- Attribution is observational. Profound data is daily with 24-48 h lag; model updates and competitor moves confound
  outcomes. Rewards, including the fixture-driven one in the lifecycle run, are not causal claims.
- EvidenceRanker was trained on Wikipedia-style claims; transfer to web pages is unvalidated beyond held-out metrics
  (~62% accuracy). Competitor change detection needs two crawls: the first crawl of a page has no diff.
- Priority is a ranking heuristic, not a revenue estimate.
- **Decision regression.** `make eval` runs 28 synthetic scenarios (adds invalid action mask, OBSERVE required, false reward) through the detector, rules, evidence gate, policy mask, verification window and reward gate. The 2026-10-03 run passed 28/28; a non-zero unsupported-confirmation, temporal-leakage or false-reward count exits 2 and blocks release. On 6 detection series: 5 true positives, 0 false positives, 1 true negative, 0 false negatives. Root-cause Top-1 and Top-3 were 3/3. Unsupported confirmations and temporal-leakage failures were 0. See [docs/EVALUATION.md](docs/EVALUATION.md). That is not a live Profound result and not a causal claim.
- "Human approval" is a process control, not authentication: the actor name comes from the unauthenticated `X-Actor`
  header (hackathon trust model; non-human actor names are refused for human-only steps). Production needs real
  authentication (OIDC/session, server-derived actor, per-role authorization) and a tightened CORS list before the API
  is exposed beyond a trusted network.
- `verify?force=true` never relaxes the verification window: before `executed_at + VERIFICATION_DELAY_HOURS` the API answers 409 `EXPERIMENT_NOT_VERIFIABLE_YET` with `details.eligible_at`.
- pgvector is provisioned but unused.
- Frontend: on 2026-10-02 `pnpm typecheck` exited 0, Vitest 34 passed and `pnpm build` completed; 48 Vitest tests are reported later (not re-run in this pass). Backend full suite: 1466 passed, 1 skipped at commit 464bf75 (1475 reported later, not re-run).

## Repo map

`backend/` FastAPI + workers + models + ML, `frontend/` Nuxt 4, `scripts/` dev tooling (fixture seed, lifecycle driver),
`docs/` architecture and handoff/integration notes, `Plan.md` product plan, `UI.md` UI contract, `THIRD_PARTY.md`.

## Provenance

Profound Lift is an original implementation developed for the Profound Marketing Engineering Hackathon. It was informed by
studying existing open-source and hackathon projects, research papers and engineering patterns in incident response,
provenance and experimentation; its AEO-specific architecture and the majority of the implementation were developed
for this project. Third-party software, datasets and models that are actually used are listed with their licenses in
[THIRD_PARTY.md](THIRD_PARTY.md); the studied work is summarised there as research inspiration only.
