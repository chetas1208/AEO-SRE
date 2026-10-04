# Profound Lift — Decision Log

This file records architectural and product decisions that materially affect implementation.

Do not rewrite historical decisions silently.

If a decision changes, add a new decision superseding the old one.

---

## DEC-001 — Product Is Profound Lift

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Profound already measures AI visibility, citations, prompt volume, and related workflows. The hackathon product has to add the missing control loop rather than clone those products.

### Decision

Profound Lift is an incident-response and adaptive control system for AI discovery.

It is not a generic marketing assistant, a dashboard-only product, a content generator, a chatbot, or a broad autonomous marketing swarm.

### Why

`Plan.md` §1. The valuable object is an investigated, approved, measured intervention.

### Alternatives Considered

A marketing dashboard. A content studio. A multi-agent campaign builder.

### Consequences

New pages and jobs have to serve the incident loop. Unrelated generators are out of scope.

### Revisit When

The product thesis in `Plan.md` changes.

---

## DEC-002 — Closed-Loop Architecture

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Detection without a measured outcome does not tell a team whether a change helped.

### Decision

The core loop is: observe → detect → investigate → prove → prioritize → propose → approve → execute → verify → learn.

### Why

`Plan.md` §2. Differentiation is the closed loop, not the chart.

### Alternatives Considered

Alerting only. One-shot LLM recommendations with no ledger.

### Consequences

Experiments may sit in `awaiting_verification` for the Profound lag (default 48 hours). That is success of the design, not a missing feature.

### Revisit When

A faster observation source exists and the delay setting should change.

---

## DEC-003 — Monorepo with `/frontend` and `/backend`

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

`Plan.md` §5 sketches `apps/web` and `apps/api`. The execution brief and the tree that was already built use `/frontend` and `/backend`.

### Decision

The application is a monorepo at the workspace root. Frontend is Nuxt 4 + TypeScript. Backend is Python 3.12 / FastAPI. Configuration is the root `.env`. `.env.example` is the committed template. Private keys are never `NUXT_PUBLIC_*`.

### Why

The running tree, `Makefile`, Compose, and Nuxt config already follow this. Moving to `apps/` would churn imports without changing the product.

### Alternatives Considered

`apps/web` + `apps/api` as drawn in `Plan.md`. Separate repositories.

### Consequences

`Plan.md` §5 folder names are historical. New code goes under `frontend/` and `backend/`.

### Revisit When

A second deployable frontend or a split service is actually required.

---

## DEC-004 — Modular Monolith

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

The loop is one product with shared transactions. Distributed infrastructure would spend the hackathon on plumbing.

### Decision

One FastAPI process, Postgres, Redis, and ARQ workers. No Kubernetes, Kafka, Temporal, or microservice split.

### Why

`Plan.md` §5 and the execution brief. Jobs are rows in `jobs` plus ARQ (`app.core.queue` → `app.workers`).

### Alternatives Considered

Temporal workflows. A queue per domain service.

### Consequences

Workers must heartbeat. A stopped worker leaves jobs `queued` (this is the current live state).

### Revisit When

A single process cannot meet a real throughput or isolation requirement.

---

## DEC-005 — Durable Domain Objects Over Agent-Centric Architecture

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Calling every step an "agent" hides state and makes the UI guess.

### Decision

Persisted objects are Organization, Signal, Incident, Evidence, Hypothesis, Intervention, Approval, Execution, Experiment, Observation, Reward, PolicyVersion, PolicyDecision, AuditEvent, and Job. Services operate on those rows.

### Why

`Plan.md` §3. Models live in `backend/app/models/`.

### Alternatives Considered

An agents table as the source of truth. In-memory investigation state.

### Consequences

API responses are these objects. The frontend must not synthesize incidents.

### Revisit When

A new durable fact appears that cannot be represented as one of these.

---

## DEC-006 — Profound Is the Observability Substrate

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Rebuilding visibility monitoring duplicates Profound and will be wrong.

### Decision

Profound supplies signals. Profound Lift adds incidents, evidence, root cause, priority, intervention choice, experiments, and policy learning. The connector normalizes responses. Domain code does not depend on Profound payload shapes.

### Why

`Plan.md` §4.1. Implementation: `backend/app/connectors/profound/`.

### Alternatives Considered

Scraping answer engines directly as the primary signal source.

### Consequences

Missing endpoints stay in the capability report. They are not stubbed with fake numbers.

### Revisit When

The hackathon account's real OpenAPI surface differs from the 0.60.2 spec the connector was built against.

---

## DEC-007 — Evidence-Gated Root Cause

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

An LLM will state a cause even when the evidence is thin.

### Decision

A hypothesis stays `proposed` until `backend/app/investigation/evidence_gate.py` accepts it. The incident machine can enter `root_cause_confirmed` only with a confirming gate result. Low evidence recommends `observe` or more investigation.

### Why

`Plan.md` §4.4. BlackBox's confirm-before-repair idea was reimplemented, not copied.

### Alternatives Considered

Trusting model confidence alone. A single similarity threshold.

### Consequences

Confirmation can fail closed. The UI must show hypotheses that are not facts.

### Revisit When

The evidence policy's required combination is too strict or too loose on real incidents.

---

## DEC-008 — Human Approval Before External Mutation

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Remediations change sites, repositories, or outreach. Those are side effects.

### Decision

GitHub mutation, CMS publication, publisher outreach, and any other external execution require a stored approval. Reject and modify are first-class. There is no auto-merge.

### Why

`Plan.md` hard rule 6. Routes: `POST /api/interventions/{id}/approve|reject|modify|execute`.

### Alternatives Considered

Auto-execute high-confidence patches. Approval as a UI-only flag.

### Consequences

Execute without approval must fail. Tests in `tests/integration/test_approval_safety.py` lock this in.

### Revisit When

A read-only action needs a lighter path. `observe` is already that path.

---

## DEC-009 — GitHub PR Is First Execution Target

**Status:** SUPERSEDED
**Date:** 2026-10-02
**Supersedes:** None
**Superseded by:** DEC-030

### Context

The first executor needs to be auditable and reversible.

### Decision

The first executor opens a GitHub branch and pull request. When `GITHUB_TOKEN`, `GITHUB_OWNER`, or `GITHUB_REPO` is missing, the executor dry-runs and says so. Profound Agent execution is later, and only against a real API.

### Why

`Plan.md` §4.8. Live capabilities currently report dry-run.

### Alternatives Considered

Writing files straight to a CMS. Merging the PR from the worker.

### Consequences

A dry-run must not create an experiment reward. The integration note in `docs/notes/integration-log.md` says dry-run executions do not verify or reward.

### Revisit When

A CMS or Profound Agent API is actually available and approved.

---

## DEC-010 — Contextual Bandits Instead of Heavy RL

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

The decision is one action per incident, with delayed reward and little data. PPO would be theater.

### Decision

Implement disjoint LinUCB and Bayesian-linear Thompson sampling. Default algorithm is `linucb` (`PolicyStore`). No PPO.

### Why

`Plan.md` §4.6. Code: `backend/app/policy/bandit.py`.

### Alternatives Considered

PPO. Epsilon-greedy only. A fixed rules engine with no logged probabilities.

### Consequences

Every decision stores a probability so IPS/DR remain possible. Open Bandit Dataset checks the math only. Do not claim the policy was trained on AEO or ZOZO outcomes.

### Revisit When

Enough measured AEO rewards exist to justify a different algorithm, still inside the same action set.

---

## DEC-011 — `observe` Is a First-Class Action

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Some changes are noise, or the cause is not proven.

### Decision

`observe` is in `ActionType` and can be the selected action. The state machine allows `root_cause_proposed → intervention_proposed` for `observe` without pretending a root cause was confirmed.

### Why

`Plan.md` hard rule 3. Cold-start priors are allowed to pick it.

### Alternatives Considered

Hiding "do nothing" and always proposing a content change.

### Consequences

The UI must be able to show `observe` as the recommendation, including during cold start.

### Revisit When

Never, unless the product thesis changes.

---

## DEC-012 — Small EvidenceRanker Instead of a Custom LLM

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

The investigation needs a calibrated support/contradiction signal. Training a foundation model is out of scope.

### Decision

EvidenceRanker is a small model: embeddings plus structured mismatch features plus a classifier. Training data is FEVER and VitaminC subsets. It does not replace Profound FactCheck. If the artifact is missing, a deterministic heuristic runs and the capability payload leaves `artifact_version` null.

### Why

`Plan.md` §4.5. Code under `backend/ml/` and `backend/app/evidence/ranker.py`.

### Alternatives Considered

LLM-as-judge for support scores. A large transformer fine-tune.

### Consequences

Metrics must come from a real training run before anyone claims accuracy. Wikipedia-trained claims do not automatically transfer to marketing pages.

### Revisit When

Held-out metrics are poor, or a smaller logistic model beats LightGBM.

---

## DEC-013 — No Fake Revenue Attribution

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Mockups show dollar impact and success probabilities the policy cannot know.

### Decision

Priority is the transparent product of prompt demand, buyer intent, severity, persona importance, competitive displacement, evidence confidence, and remediation feasibility, normalized 0–100. No currency amounts. Expected outcome ranges appear only from evaluated experiments of the same class; otherwise the UI says history is insufficient.

### Why

`Plan.md` hard rule 5 and `UI.md` conflict resolutions in §8 and §10.6.

### Alternatives Considered

A dollar "opportunity" figure. Static "+8–15pp" copy from the mockup.

### Consequences

`ExpectedOutcomePanel` must stay empty of invented ranges.

### Revisit When

A real revenue feed exists and a causal method is specified.

---

## DEC-014 — Delayed Reward Is Real

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Profound prompt data lags by about a day. An immediate "success" would be fabricated.

### Decision

Until post-intervention observations exist, the experiment stays `awaiting_verification` and reward stays unset. `VERIFICATION_DELAY_HOURS` defaults to 48. Reward components are stored separately.

### Why

`Plan.md` §4.7 and hard rule 4.

### Alternatives Considered

LLM-judged immediate reward. Treating PR creation as success.

### Consequences

The experiments screen must render a pending verification state without a reward number.

### Revisit When

The observation lag is measured and the delay setting should change.

---

## DEC-015 — Policy Versions Are Immutable

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

If policy state is overwritten, past experiments cannot be explained or evaluated off-policy.

### Decision

`PolicyVersion` rows are immutable (`ImmutableVersionError`). A reward update writes a new version. `PolicyDecision` records the version that chose the action.

### Why

`Plan.md` hard rule 7. Model: `backend/app/models/policy.py`.

### Alternatives Considered

One mutable policy blob.

### Consequences

Live capability correctly says no version exists yet, rather than inventing `policy_v1` with fake learning.

### Revisit When

Retention of old versions becomes a storage problem. Do not delete versions that experiments reference.

---

## DEC-016 — Evidence Provenance Is Mandatory

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

A graph drawn in the browser from sample nodes would look like an investigation.

### Decision

Evidence stores source, retrieval time, content hash, excerpt or structured extract, provenance, support and contradiction scores, freshness, and confidence. Graph edges carry the same kind of provenance. The frontend renders the API graph.

### Why

`Plan.md` §4.4. Models: `backend/app/models/evidence.py`.

### Alternatives Considered

A client-only diagram. Unhashed page text.

### Consequences

Empty evidence is an empty graph, not a placeholder DAG.

### Revisit When

Snapshot bodies outgrow Postgres and need object storage. Hashes and refs stay on the row.

---

## DEC-017 — No Demo-Only Production Path

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Hackathon demos often ship hardcoded incidents. That would make the loop untrustworthy.

### Decision

No `DEMO_MODE`, no `/demo` route, no hardcoded winning incident, no fake Profound body, no fake reward. Test fixtures and `scripts/seed_dev_fixture.py` are allowed only when labeled and kept off the live path. The current live incident titled `x` is leftover local data, not a supported demo.

### Why

`Plan.md` hard rule 1. `tests/unit/test_no_demo_mode.py` exists.

### Alternatives Considered

A recorded JSON scenario presented as live.

### Consequences

If Profound is down, the UI shows unavailable. It does not fall back to the `x` incident as a story.

### Revisit When

Never for the production path. Test fixtures can grow.

---

## DEC-018 — Frontend Is Intentionally Small

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

`UI.md` contains a wide mockup and a narrower implementation spec. `Plan.md` §6 names three routes. `UI.md` §2.1 and §10.7 settle the navigation.

### Decision

Top-level navigation is Incidents, Experiments, and Settings. Incident detail holds the evidence graph. There is no Dashboard, Chat, Knowledge Graph, Content Studio, or Agent Builder route.

### Why

`UI.md` §2 and §10. The pages on disk match this set. `/` redirects to `/incidents`.

### Alternatives Considered

Mockup A's ten-item nav.

### Consequences

New UI work goes inside these routes. Stretch pages stay unbuilt.

### Revisit When

`UI.md` explicitly adds a route.

---

## DEC-019 — UI Wiring Before Visual Polish

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

The mockup is visually dense. The loop is not finished.

### Decision

Build routing, API data, state transitions, SSE, approvals, and empty/error/loading states before a CSS pass. `UI.md` remains the visual contract for a later pass. Do not invent numbers to match the mockup.

### Why

`UI.md` opening implementation spec and `Plan.md` §6.

### Alternatives Considered

Pixel-matching the mockup first.

### Consequences

Screens can look plain while data is correct.

### Revisit When

Milestone 10's functional loop works on the live stack.

---

## DEC-020 — Reference Repositories Stay Isolated

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Seven hackathon repos inform the design. Coupling the app to them would freeze their bugs and licenses into production.

### Decision

Clones live in `references/` and are gitignored. Production code does not import them. They are not submodules.

### Why

Execution brief §18 and §20. `.gitignore` already ignores `references/`.

### Alternatives Considered

Git submodules. Copying a backend package in place.

### Consequences

A fresh clone of this repo does not contain the references. License notes must live in-repo (`docs/notes/` and `THIRD_PARTY.md`).

### Revisit When

A dependency is published as a real library we choose to vendor.

---

## DEC-021 — License Verification Before Reuse

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Not every reference has a license that allows copying.

### Decision

No direct code reuse without a current LICENSE that permits it. Record repository, SHA, license, files, and attribution in `THIRD_PARTY.md`. Absent or hackathon-only licenses mean reimplement the idea.

Checked SHAs and results are in `Progress.md`. As of this decision, no reference code was copied. BlackBox is Apache-2.0 and CampaignPilot is MIT, so copying was legally possible and was still declined because a literal port would drag in the wrong domain.

### Why

Execution brief §20. Notes: `docs/notes/third-party-a2.md` through `a6.md`, plus lattice/recoil/slap README checks.

### Alternatives Considered

Copying BlackBox modules and renaming DataHub types.

### Consequences

Future agents must not "speed up" by pasting `references/**` into `backend/`.

### Revisit When

Someone proposes a specific file copy. Re-read that repo's LICENSE at the pinned SHA first.

---

## DEC-022 — Incident-Machinery Pattern Study and Independent Implementation

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Studied prior open-source incident-response systems (including BlackBox) to examine patterns around staged repair, evidence gates, human verification, and event streaming.

### Decision

Design and implement Profound Lift's incident lifecycle, state machine, and evidence verification independently from scratch for the AI-discovery problem. Do not copy code or import external stacks.

### Why

`docs/notes/blackbox.md`. Profound Lift requires an explicit state machine edge map, strict transactional rollback, and AI-search domain semantics rather than generic SQL repair.

### Alternatives Considered

Porting an external incident engine. Declined because a literal port would drag in incompatible domain abstractions.

### Consequences

The implementation is entirely original. Third-party research inspiration is acknowledged in `THIRD_PARTY.md`.

### Revisit When

Never; the independent state machine is already in production and verified.

---

## DEC-023 — Layered Root-Cause Analysis Architecture

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Studied multi-level diagnostic patterns in prior marketing and observability systems (such as CampaignPilot) to evaluate how multi-system anomalies are categorized.

### Decision

Develop an independent, multi-layer root-cause engine tailored specifically to AI discovery across six distinct layers: AI engine, citation, owned content, competitor, external web, and canonical truth. Keep a fixed action vocabulary and deterministic rule fallbacks.

### Why

`docs/notes/campaignpilot.md`. AEO search anomalies require domain-specific evidence correlation across search engine updates, citation graphs, and competitor crawl changes. All detector and RCA modules are written independently for Profound Lift.

### Alternatives Considered

A single unstructured LLM prompt with no layer structure.

### Consequences

Hypotheses carry a layer. Deterministic rules remain the reliable baseline when the LLM is unconfigured or unavailable.

---

## DEC-029 — Cost-Aware Model API Routing (FAST and DEEP Tiers)

**Status:** ACCEPTED
**Date:** 2026-10-03
**Supersedes:** None

### Context

Profound Lift is an incident-response and experimental-learning system. Deterministic code handles metrics, incident state, evidence existence, verification timing, reward, policy updates, and approval. The model is only needed for structured semantic operations: prompt intent classification, claim extraction, evidence summarization, candidate hypothesis generation, counterevidence assessment, intervention drafting, and incident explanations. Sending every request to an expensive frontier model wastes API credits and increases latency without improving decision quality.

### Decision

Implement a two-tier cost-aware `ModelRouter` inside the provider-neutral `ModelGateway`:
1. **FAST Tier (Default):** Claude Haiku 4.5 ($1/M input, $5/M output) handles ~80–95% of semantic calls.
2. **DEEP Tier:** Claude Sonnet-class model (`claude-sonnet-4-6`, $3/M input, $15/M output) is used only when an incident's deterministic complexity score exceeds the threshold (`model_complexity_threshold >= 0.65`) or when FAST tier structured validation fails after retry.
3. **No Frontier Models by Default:** Opus is not used for normal operation.
4. **Deterministic Gate Remains:** Claude proposes candidate hypotheses, but only the backend evidence gate confirms root causes.
5. **Cost & Usage Telemetry:** Every call records latency, tokens in/out, tier, escalation flag, and estimated spend USD.

### Consequences

Model operational costs are reduced by ~70–80%, latency is halved for routine classifications and drafts, and provider neutrality is strictly preserved across adapters.

### Revisit When

A layer cannot express a real incident class.

---

## DEC-024 — Info-Ninja, LaunchPilot, Lattice, Recoil, and SLAP Are Concept References Only

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

These repos informed web research, approval tables, provenance, structured analysis, and the marketing-to-GitHub handoff. Info-Ninja, LaunchPilot, Lattice, and Recoil have no usable license. SLAP's README limits the project to HackUPC.

### Decision

Reimplement the ideas. Do not copy their source.

- Info-Ninja: multi-source fetch, cache, streamed progress.
- LaunchPilot: approval row then execute.
- Lattice: derived values stay tied to inputs; failed parse yields empty, not a guess.
- Recoil: structured analysis objects, not an essay.
- SLAP: an operator can approve a remediation without using Git by hand.

### Why

License gate. SHAs in `Progress.md`.

### Alternatives Considered

Copying LaunchPilot's SQLAlchemy models (rejected: no license).

### Consequences

Similarity of shape is fine. Identical source is not.

### Revisit When

Upstream adds a license and a copy would save more than it would cost to adapt.

---

## DEC-025 — Fail Explicitly, Never Hallucinate

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Missing integrations are the normal hackathon state.

### Decision

Unavailable Profound, crawl, model, GitHub, artifact, or post-intervention data is reported as unavailable, degraded, dry-run, or awaiting verification. Do not fill gaps with plausible JSON.

### Why

`Plan.md` §7. Live `/api/system/capabilities` already follows this.

### Alternatives Considered

A demo fallback payload when keys are empty.

### Consequences

The UI has to render degraded and empty states. Overall capability `degraded` is an honest summary, not an outage of the API.

### Revisit When

Never as a principle.

---

## DEC-026 — Incident and Experiment Status Vocabularies

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

`Plan.md` names experiment states `proposed → approved → executed → awaiting_reward → evaluated`. The execution brief and `backend/app/domain/enums.py` use a finer incident machine and `awaiting_verification` / `verified` / `rewarded`.

### Decision

Use the enums already in code.

Incident states: `detected`, `triaged`, `investigating`, `evidence_ready`, `root_cause_proposed`, `root_cause_confirmed`, `intervention_proposed`, `awaiting_approval`, `approved`, `executing`, `executed`, `awaiting_verification`, `verified`, `rewarded`, `closed`, `dismissed`, `failed`.

Experiment states: `proposed`, `approved`, `executing`, `executed`, `awaiting_verification`, `verified`, `rewarded`, `rejected`, `failed`.

`awaiting_verification` is the plan's `awaiting_reward`. Reward stays null in that state. `evaluated` is not a status; a rewarded experiment is the evaluated one.

Transitions are the edge map in `backend/app/incidents/state_machine.py`. UI labels may be friendlier (`UI.md` §26) but API values stay these strings.

### Why

One vocabulary has to be persisted. The state machine and tests already use this one. It is stricter than the plan's sketch and matches the execution brief.

### Alternatives Considered

Renaming code to the shorter plan words and losing the split between verified and rewarded.

### Consequences

Do not add `awaiting_reward` as a second status. Map it in docs and UI copy only.

### Revisit When

A client already depends on the plan's shorter names. None does.

---

## DEC-027 — Action Vocabulary Names

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

`Plan.md` §4.6 uses short names (`update_page`, `new_canonical_page`, `faq`, `comparison_content`). The execution brief and `ActionType` use longer names.

### Decision

The persisted action strings are:

`observe`, `update_existing_page`, `create_faq`, `create_canonical_page`, `create_comparison_content`, `publisher_outreach`, `structured_data`.

The model cannot add actions.

### Why

`backend/app/domain/enums.py` and the policy module already use these. They are unambiguous next to UI copy.

### Alternatives Considered

The shorter plan aliases as the stored values.

### Consequences

APIs and policy state use the long names. UI copy can say "Update existing page".

### Revisit When

A seventh remediation type is required. Add it to the enum, priors, and tests together.

---

## DEC-028 — Queue, ORM, Logging, and API Shape

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Several ordinary stack choices were already implemented and should not be relitigated.

### Decision

- ORM: SQLAlchemy 2, Alembic revision `0001`. `init_db.create_all` is a dev helper, not the schema authority (the live database still violates this; fixing that is work, not a new design).
- Queue: Redis + ARQ, with a `jobs` row per enqueue.
- API: `/api/...`, snake_case JSON, no URL version prefix yet.
- Errors: structured `error.type` / `message` / `details` / `request_id`.
- Logging: structlog, JSON in production.
- Frontend server state: Nuxt composables and `useApi`. Pinia only for organization, incident selection, and live system flags.
- HTTP crawl limits and SSRF checks stay in `backend/app/connectors/web/`.
- Tests: pytest against Postgres when reachable, sqlite fallback, external HTTP mocked.

### Why

These are already the code paths in `backend/app/core/`, `backend/app/api/`, and `frontend/composables/`.

### Alternatives Considered

Dramatiq. A `/api/v1` prefix. Global Pinia stores for every response.

### Consequences

New routes join the existing routers. New jobs go through `enqueue` and `HANDLERS`.

### Revisit When

A breaking public API needs a version prefix.

---

## DEC-029 — OpenAPI Types Are Generated, Not Hand-Copied

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Frontend/backend drift was called out as an integration risk.

### Decision

`frontend/package.json` script `gen:api` writes `frontend/types/api.generated.ts` from the live OpenAPI document. Hand-written helpers may adapt payloads, but they should not invent fields the API does not return.

### Why

`frontend/types/api.generated.ts` already exists. The script uses `NUXT_PUBLIC_API_BASE_URL`.

### Alternatives Considered

A shared JSON schema package. Duplicated TypeScript interfaces maintained by hand.

### Consequences

After API schema changes, regenerate types and typecheck. This session has not regenerated or typechecked.

### Revisit When

The generated file becomes too large to review and a thinner client is needed.

---

## DEC-030 — Execution Is Executor-Neutral

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** DEC-009

### Context

DEC-009 made a GitHub pull request the first execution target because BlackBox repairs code. Profound Lift does not. The product records an approved intervention as an experiment and measures the AI-discovery outcome. Where the change is applied is an adapter.

### Decision

The core lifecycle is executor-neutral. Human approval is mandatory before any external mutation. The default executor is `ManualExecutor`: it writes an intervention package, mutates nothing, and waits for a human to record that the change was applied. `observe` activates an experiment with no human content change.

Optional adapters, only when implemented and configured:

- `GitHubPRExecutor` — opens a pull request, never merges. Requires `GITHUB_TOKEN`, `GITHUB_OWNER`, and `GITHUB_REPO`. Not required to boot or to complete the loop.
- `ProfoundAgentExecutor` — stub only. It refuses execution. No Profound action API is integrated.

Root `.env` does not require GitHub settings. `.env.example` lists them under optional executors, commented out.

### Why

The thesis is signal → incident → evidence → policy → approval → experiment → reward. A pull request is one way to apply a content change, not the definition of execution.

### Alternatives Considered

Keeping GitHub as the only real executor and treating manual completion as a dry-run. That blocked the loop whenever credentials were absent and taught the policy nothing from real human execution.

### Consequences

A manual execution is a real execution (`dry_run=False`). Only a GitHub preview is a dry-run, and dry-runs do not earn reward. Milestone 8 is "Intervention Approval + Experiment Activation," not "create a PR." Missing GitHub credentials do not block Milestone 10.

### Revisit When

A documented Profound Agent or CMS API can apply an approved change. Add that adapter beside `ManualExecutor`. Do not replace the manual path.

---

## DEC-031 — Ingest On Profound's Cadence

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Tracked prompts refresh daily, and new prompts need about 24–48 hours before measurements accumulate. Polling Profound every 30 minutes restates the same window and spends the 600 request/hour budget without new observations.

### Decision

Scheduled ingest and detect run at 01:15, 07:15, 13:15, and 19:15 UTC. Creating an organization still ingests immediately. Verification stays on a 15-minute check because it only calls Profound for experiments whose lag window has opened. `VERIFICATION_DELAY_HOURS` remains 48. Historical replay uses only signals observed at or before the chosen time and does not write a live incident.

### Why

The product should wait for real measurements. A tighter poll does not make delayed data arrive sooner.

### Alternatives Considered

Leaving the half-hour cron. It is safe because upserts are idempotent, but it is not aligned with the data.

### Consequences

A key added between scheduled slots does not wait for the next slot if the operator creates or re-saves an organization: that path enqueues ingest immediately. The capabilities payload includes `next_ingestion`. `/api/health` reports whether Profound and the model provider are configured. Those fields do not change the health status code.

### Revisit When

A Profound account shows that a surface updates faster than daily, or the request budget cannot support four windows.

---

## DEC-032 — Query Fanouts Are Evidence, Not Alerts

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Profound's query-fanout report lists the searches an answer engine runs for a tracked prompt. Most day-to-day wording changes are noise. A material change in those searches can explain a competitor appearing even when our page did not change.

### Decision

Ingest `POST /v2/reports/query-fanouts` as `fanout_share` signals. Do not add `fanout_share` to the incident detector. During investigation, compare the latest date of each prompt and query with earlier dates. Record evidence only when share moves by at least 0.15 (or 15 when values look like percents), or when a new query names a configured competitor at that share. The RCA rule `query_interpretation_shifted` stays `proposed`. An owned page that also changed is stored as contradicting evidence and lowers the heuristic confidence. The evidence gate is unchanged.

### Why

The useful question is whether the engine reinterpreted the buyer question. Alerting on every query variation would bury that.

### Alternatives Considered

A new incident category for every fanout delta. Treating fanout text as proof of root cause without the evidence gate.

### Consequences

Without a Profound key this path writes nothing. Competitor matching uses the first label of `competitor_domains` (for example `okta` from `okta.com`), not a fuzzy name match. Fanout evidence alone does not confirm a hypothesis.

### Revisit When

A live account shows share is reported on a scale other than a fraction or a percent, or competitor names are not recoverable from domains.

---

## DEC-033 — Decision Quality Is a Deterministic Eval

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

A passing test suite shows that functions do what the tests say. It does not, by itself, show that detection, root cause, and action choice stay conservative on noisy series.

### Decision

`make eval` runs `python -m evals`. It calls the production detector, rule RCA, evidence gate, cold-start `rule_fallback`, and URL guard on a fixed synthetic set. Output goes to a gitignored `experiments/evals/<timestamp>/`. Live model output is a separate mode and was not run. Profound answer rows are available through `ProfoundClient.answers` for an investigation, not through the scheduled ingest.

### Why

The question this has to answer is whether the system invents a story around noise. A small labeled set with negative cases is the check we can run without a Profound key.

### Alternatives Considered

An LLM judging another LLM. Treating the FEVER accuracy number as an AEO quality score.

### Consequences

The report's counts are the size of this fixture set. They are not a false-positive rate for production traffic. Reward false-success stays in the Postgres reliability tests. EvidenceRanker metrics are read from the existing held-out file and are not recomputed by `make eval`.

### Revisit When

A real Profound history export is available to add as a sanitized replay scenario, or a scenario in this set fails for a reason that is a product bug rather than a bad expectation.

---

## DEC-034 — Explanations and Causal Language Stay Qualified

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

Incident detail was assembled on the client from raw fields. Verification could record a favorable before/after move without saying whether a confounder was present.

### Decision

The API returns `IncidentExplanation`. The page renders that object. A hypothesis with `actionable: false` or rule `no_actionable_cause` is not presented as the leading cause. Policy alternatives are labeled `policy score`.

`qualify_association` never returns causal confidence `high`. A recorded confounder forces `low`. The verified transition reason states a before/after association, not a demonstrated cause. Confounders travel on the observation as `_confounders` and are stripped from stored `after_metrics`.

### Why

The product question is whether the system is telling a story around a metric move. The explanation and the causal statement have to be produced by the same code the eval checks.

### Alternatives Considered

Letting the frontend pick a hypothesis. Writing "caused" when the reward is large.

### Consequences

Existing experiments are not backfilled. The statement is appended only when verification accepts an observation. Reward remains an association score.

When the evidence gate has not confirmed a cause, the policy is constrained to `observe`. The explanation states that constraint. Numbers shown for other actions are unconstrained policy probabilities, not the selection.

### Revisit When

A method exists that can support a higher causal confidence from this data. Until then, `high` stays unreachable.

---

## DEC-035 — Remaining Decision Backlogs Stay Inside the Existing Loop

**Status:** ACCEPTED
**Date:** 2026-10-02
**Supersedes:** None

### Context

The evaluation campaign left several decision-quality items unimplemented: rejection reasons, a human override record, an acceptance rate, an investigation budget, incident-only answer detail, and a developer trace. Profound's answer SSE stream exists. Scheduled ingest already works paginated.

### Decision

Rejection uses a closed set of reason codes. The audit row stores the recommended action, the action the human decided, and the reason. `feeds_reward` is false. Acceptance rate is reported on `GET /api/policy` and is not an input to the bandit.

Investigation reads `max_web_sources`, `max_hypotheses`, `max_model_calls`, and `max_investigation_seconds`. A step past the time limit is skipped and the investigation is incomplete. No hypothesis is invented to fill that gap.

`POST /v2/prompts/answers`, including `citation_details`, runs only from investigation, and only when a Profound key is configured. No key produces zero requests and zero rows. The SSE answer stream is not used.

Postgres detection takes a transaction advisory lock per organization before the duplicate check. That does not add a new unique column.

`python -m app.devtools.trace <id>` prints the stored chain in development. It is not an API route.

### Why

These were the remaining ways the system could look more confident than its evidence. They belong on the existing incident, approval, and investigation path.

### Alternatives Considered

Replacing paginated answers with the SSE stream. Treating rejection reasons as negative rewards. Adding a public debug page.

### Consequences

A live Profound account is still required before answer details contain real rows. Acceptance rate with no decided approvals is null. Milestone 3 stays blocked without a key.

### Revisit When

A sanitized Profound history export exists, or rejection reasons are given an explicit learning design.

---

## DEC-036 — Live Cutover Must Not Silently Use Fixture Data

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

Milestone 3 and 10 depend on real Profound data. Fixture seeding is required for local demos but must never masquerade as production observability.

### Decision

- `scripts/seed_dev_fixture.py` refuses `ENVIRONMENT=production`.
- Live ingest (`ingest_org`, worker cron) returns `unavailable` when Profound is not configured; it does not substitute fixture signals.
- Signal provenance is explicit via `Signal.source` and UI badges (`dev_fixture` → Test fixture).
- Verification windows are enforced in `pipeline.verify` (`now < verification_window_start` → `awaiting_window`); rewards are not written early.

### Consequences

Operators add `PROFOUND_API_KEY` and run `make profound-smoke` / `make ingest-live` for first live validation. Browser E2E and delayed experiment completion remain external blockers until runtime/credentials exist.

### Revisit When

Live Profound ingest succeeds and fixture org is clearly separated from production orgs in ops runbooks.



---

## DEC-037 — Model Runtime Is a Provider-Neutral Gateway

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None (refines the optional LLM path in DEC-025)

### Context

The final model provider is unknown. Hard-wiring one vendor SDK into investigation or drafting code would force a rewrite at cutover and let provider quirks leak into domain logic.

### Decision

- Domain code calls only `ModelGateway` (`generate_structured`, `generate_text`, `health`). No provider SDK or provider URL is imported outside `app/connectors/llm`.
- One active runtime is configured with generic variables: `MODEL_PROVIDER` (label), `MODEL_API_PROTOCOL` (`openai_chat`, `openai_responses`, `anthropic_messages`), `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`. `create_model_gateway(settings)` returns an OpenAI-compatible adapter (OpenAI, NVIDIA NIM, vLLM through their base URL) or the native Anthropic adapter. There is no vendor-specific adapter unless a provider proves incompatible.
- Requests are provider-neutral (system, messages, temperature, max output tokens, response schema, purpose, prompt version). State-affecting purposes run at low temperature. Structured calls are validated with Pydantic, get one repair attempt, then fail explicitly. Retries are bounded and only for timeout, 429 and 5xx.
- Untrusted web text is passed as delimited data. Evidence is cited by immutable id; unknown ids are discarded. A URL the model mentions that the fetch subsystem did not retrieve never becomes evidence. The model never chooses the action, never confirms a root cause and never touches state, timestamps, reward or authorization.
- Health is `NOT_CONFIGURED`, `READY`, `AUTH_FAILED` or `DEGRADED`. A missing or failing provider degrades the investigation to rules only. Nothing else stops.
- Only adapter-tested providers are documented. Nothing has been called live.

### Alternatives Considered

One adapter per vendor. A single vendor SDK. A framework such as LangChain. An LLM that selects actions or confirms causes.

### Consequences

Switching provider is a configuration change plus `make model-smoke`. Native structured-output and tool-calling modes are deliberately unused until a real provider needs them.

### Revisit When

A live provider is chosen and `make model-smoke` shows a protocol quirk the generic adapters cannot absorb.

---

## DEC-038 — OBSERVE Is a Measured No-Remediation Experiment

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None (extends DEC-011)

### Context

`observe` must be a real option, not an absence of decision. It needs a defined outcome so the policy can learn what happens when nothing is done.

### Decision

- Activation of an OBSERVE experiment is its execution: no approval, no package. The window and measurement rules are the same as for any intervention.
- The outcome label comes from the metric declared before activation. Gain is `(after - before)` in the declared direction. At least +0.02 is FAVORABLE (`self_recovery`), at most -0.02 is UNFAVORABLE (`worsened`), and anything between is NEUTRAL (`persistent`). A missing metric, an uncomputable reward, or a hard confounder is INCONCLUSIVE.
- Hard confounders are an overlapping intervention on the same target or cluster, platform-wide movement explaining at least 80% of the change, and an OBSERVE baseline disturbed by an intervention. Soft confounders are stored and lower causal confidence. Causal confidence is `low` or `medium`, never `high`, and every statement ends "before/after association, not a demonstrated cause".
- OBSERVE reward is `compute_reward` with action cost 0. It is the policy's no-action baseline for similar contexts, not a control group for other actions. Rewards of other actions can include the same background recovery and the system does not claim otherwise.
- INCONCLUSIVE writes the outcome row only: no reward and no policy version.
- The 0.02 band and the 80% rule are hand-set constants, not calibrated on real Profound data.

### Alternatives Considered

Treating no action as a missing row. Using OBSERVE as a randomized control. Calling outcomes causal.

### Consequences

The fixture experiment (OBSERVE, awaiting verification) will produce an honest label once its window opens on 2026-10-04T20:29:43Z, or INCONCLUSIVE if the data does not support one.

### Revisit When

Enough real outcomes exist to calibrate the noise band, or a holdout design is approved.

---

## DEC-039 — One Verification Window, Retry-Safe Mutations, Central Error Mapping

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None (hardens DEC-014 and DEC-036)

### Context

Verification eligibility was implemented in several places, `force=true` could be read as relaxing it, a repeated approve returned an error that looked like a failure, and domain errors reached clients as generic 409/500 bodies.

### Decision

- `app/experiments/window.py` is the only definition of eligibility: an attempt is allowed when `now >= executed_at + delay` (start inclusive); a measurement counts when its source `observed_at` is at or after the window start, after execution, and not in the future. Dry runs never verify. Time comes from an injected clock. `force` never relaxes any of it.
- `POST /api/experiments/{id}/verify` before the window answers 409 `EXPERIMENT_NOT_VERIFIABLE_YET` with `details.eligible_at`, queues nothing, and a repeat returns the in-flight job.
- Approve and reject are retry-safe: repeating the recorded decision returns the recorded outcome; a different decision is 409. Approval and the experiment ledger link are one transaction. Concurrent mutations on one intervention or experiment are serialized by a Postgres advisory lock.
- Domain errors carry a stable `code` and are mapped to HTTP in one place. Error bodies are `{"error": {"code", "type", "message", "details", "request_id"}}` with no stack trace. A database outage is 503 `DATABASE_UNAVAILABLE`.
- Health reports database, Redis, Profound and model independently. Only the database makes it 503. Redis down is `degraded`. Unconfigured Profound or model never degrades it.

### Alternatives Considered

Letting `force` bypass the lag. Making a repeated approve an error. Handling each domain exception in each route.

### Consequences

The UI cannot trigger an early verification. Clients can retry safely. Operators get one error vocabulary.

### Revisit When

Real authentication replaces the trusted `X-Actor` header.

## DEC-040 — Provenance Framing: Studied, Not Copied

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None (reframes the public wording of DEC-020, DEC-022, DEC-023 and DEC-024; those records stay as written history)

### Context

Earlier decisions called some studied hackathon projects "primary references". Read alone, that wording could suggest the product was built on them. An audit on this date compared all seven studied repositories with production source and found no copied code; overlaps were limited to import blocks, the standard Alembic `env.py` template and one-line framework idioms. Production code does not import or read `references/`.

### Decision

- Public-facing text describes Profound Lift as an original implementation developed for the Profound Marketing Engineering Hackathon, informed by studying existing open-source and hackathon projects, papers and engineering patterns.
- `THIRD_PARTY.md` separates "Incorporated third-party software, data and models" (what is used or distributed, with licenses) from "Research and architectural inspiration" (studied only). Commit SHAs and license findings for studied projects live in `docs/notes/research-inspiration.md`.
- Studied projects are not named as components, engines or donors in architecture text or source comments. Dataset, paper and API citations stay.
- No claim is made that no outside work influenced the project, and none that it is entirely novel.
- If code is ever copied, its license notice is added to `THIRD_PARTY.md` first.

### Alternatives Considered

Removing all mention of studied projects (rejected: understates the research). Keeping "primary reference" wording (rejected: overstates reliance).

### Consequences

Historical ADRs, the build brief and handoff notes are unchanged records of how the work was organised.

### Revisit When

Any third-party source file is added to the repository.

---

## DEC-041 — Change Guard as a Feature, Not a Pivot

**Status:** ACCEPTED (proposal stage; implementation tracked in Milestones, "Change Guard")  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

Two nearby directions were considered and set aside. First, a pivot toward incrementality measurement (is a visibility change caused by an action?) was abandoned: it would have replaced the incident-response loop rather than strengthened it, and the project's existing measurement is deliberately observational (DEC-034). Second, a "Merge" idea (a queue that coordinates changes proposed by several agents) was considered as a standalone product.

Meanwhile the existing system already has the pieces that make a narrower version valuable: experiments with a single verification window (DEC-039), collision rules between experiments (B4), and an evidence/contradiction ranker. A change that lands on a page while an experiment is still being measured contaminates the measurement; two agents proposing different claims for one page, or a claim that contradicts what the organisation says is true, are failures that a per-run review step does not see because they only exist across runs.

### Decision

Build **Change Guard** inside Profound Lift as a capability that protects the Experiment object and checks changes proposed by Profound Agents (and Profound Lift's own interventions): an Agent posts a structured ChangeSet through a Call API node and receives one decision (ALLOW, MERGE, DELAY, REQUIRE_REVIEW, BLOCK) based on exactly three checks: active-experiment contamination, duplicate/conflicting target changes, canonical-truth conflict. Approvals bind to a digest of the proposal. Specification: `docs/CHANGE_GUARD_SPEC.md`; integration: `docs/CHANGE_GUARD_INTEGRATION.md`; evaluation: `make eval-guard`.

The guard targets **cross-run coordination** (this change vs other active changes vs canonical truth vs active experiments). It makes no claim about what Profound's own review features do or lack.

### Alternatives Considered

- Pivot the product to incrementality: rejected (replaces the loop; claims we cannot support).
- Standalone "merge queue" product now: rejected (no real multi-agent ChangeSet traffic exists to design against; it would be speculation).
- Do nothing: rejected (experiment contamination is a known weakness of the current loop).

### Consequences

- New persistence (migration 0004), an authenticated public-facing endpoint, and canonical-truth data that humans must maintain.
- Demonstrations use SIMULATED agents and are labelled as such; no live Profound Agent traffic has been seen.
- The endpoint must be reachable from Profound's cloud; that is unsolved here (tunnel or deployment needed).
- Semantic checks degrade honestly: when the model/ranker is unavailable the response says `degraded`; empty canonical truth is reported as skipped, never as a pass.

### Revisit When / Graduation Criterion

Revisit as a standalone product only if the system receives **enough real ChangeSets from multiple Profound Agents** to make cross-agent coordination a recurring problem. Until then it stays a feature.

**Update 2026-10-03 (D2 docs pass; the record above is unchanged).** Implementation status at this date: the guard service, routes, migration `0004_change_guard`, UI and test suites are in the tree (commit `ac84d54`) and are IMPLEMENTED; none is LIVE VERIFIED. The server running on :8000 predated this code (`git_sha=ae05b01`, no `/api/change-checks` in its OpenAPI). `make eval-guard` at 2026-10-03T20:50Z passed 22/24 scenarios and reported release_blocked (target-variant false ALLOW and approval-digest binding), so the guard's own gates were not yet green. The "Profound Merge" standalone idea remains deferred under the graduation criterion above.

---

## DEC-042 — Incrementality / Causal Pivot Dropped

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None (sharpens the "pivot rejected" paragraph of DEC-041; DEC-034 observational framing stays in force)

### Context

A pivot toward incrementality/causal measurement was explored and agents built part of it. A Profound engineer's feedback, as relayed by the user (not independently verified here), was that incrementality economics are hard for smaller teams. The user then explicitly dropped the direction.

### Decision

Do not wire any incrementality or causal-lift measurement into the product. The partial work was removed from the tree and kept as a backup outside the repository (`~/aeo-causal-pivot-wip-backup`: `tracked-changes.patch`, `untracked-files.tgz`, `untracked-list.txt`). Attribution stays observational and qualified (DEC-034). Empty directories `backend/tests/causal` and `backend/tests/evals_causal` are leftovers on disk, not part of the product.

### Alternatives Considered

- Finish and ship the causal module: rejected by the user.
- Keep it behind a flag: rejected; unreachable code claims capability the product does not have.
- Delete the work outright: rejected; the backup preserves it cheaply.

### Consequences

No claim of causal lift or incrementality anywhere in README, UI or docs. Rewards remain measured, observational and confounded.

### Revisit When

The user re-opens the direction with a measurement design that small teams can afford and a data source that supports it.

---

## DEC-043 — Neo4j as a Rebuildable Provenance Projection; Postgres Stays the System of Record

**Status:** ACCEPTED, implementation IN PROGRESS (infrastructure by agent N1; nothing projected; no live connection verified at this date)  
**Date:** 2026-10-03  
**Supersedes:** None (Plan.md's "no giant knowledge-graph UI" and the single-Postgres architecture are unchanged for the transactional path)

### Context

Change Guard, experiments, agents and decisions form a relationship-heavy history (which agent run produced which change, which decision protected which experiment). Answering lineage and contention questions is natural in a graph. Binding spec: `docs/GRAPH_SPEC.md`.

### Decision

- PostgreSQL remains the system of record for transactions, idempotency, state machines, approvals, experiments, rewards and policy versions. Neo4j is a rebuildable projection and is never required for approval, activation, verification, reward or any state transition.
- Write Postgres first, then project through a Postgres outbox (`graph_outbox`) after commit; no Kafka. Projection is idempotent (`MERGE` on business ids, never Neo4j internal ids), checkpointed, replayable and auditable against Postgres.
- Every node carries `organization_id` and an app marker; every query is organization-scoped, parameterized and bounded. The browser never talks to Neo4j; credentials never go in `NUXT_PUBLIC_*`.
- If Neo4j is down, unconfigured or stale, the product keeps working and the policy falls back to the deterministic BaselinePolicy; graph health is reported separately and does not fail the API.
- Provenance relationships only (`TRIGGERED_BY`, `DERIVED_FROM`, `BASED_ON`, `FOLLOWED_BY`, `ASSOCIATED_WITH`); no `CAUSED_BY` from temporal order alone. Real Profound identifiers are preserved and never invented; SIMULATED/FIXTURE/TEST data stays labelled.

### Alternatives Considered

Neo4j as primary store (rejected: transactional guarantees live in Postgres); Postgres recursive queries only (viable for shallow lineage, weaker for multi-hop contention and similarity); Kafka for the event path (rejected: scale does not justify it).

### Consequences

A second datastore with an outbox to operate and a reconciliation command; the graph can lag. Claims about the graph are limited to what is projected and verified against the hosted instance.

### Revisit When

Projection lag or outbox backlog becomes a recurring problem, or the graph features do not improve held-out evaluation (then keep the graph for lineage only).

---

## DEC-044 — Control Policy: Deterministic Mask First, Graph Bandit in Shadow

**Status:** ACCEPTED, implementation IN PROGRESS  
**Date:** 2026-10-03  
**Supersedes:** None (extends the existing LinUCB policy and its eligibility mask)

### Context

The Change Guard decision (ALLOW/MERGE/DELAY/REQUIRE_REVIEW/BLOCK) could be informed by graph context, but safety rules must not be learnable away.

### Decision

- Pipeline: ChangeSet -> deterministic safety engine -> eligible actions -> feature encoder -> contextual bandit -> recommendation -> human. The learner chooses only among eligible actions; hard rules (canonical contradiction, protected-target collision, invalid digest) always win.
- The existing rule policy is kept as `BaselinePolicy` and is never deleted. `GraphLinUCB` (versioned feature schema `graph_context_v1`) runs in **shadow mode first**; BaselinePolicy stays active. Graduation requires a minimum number of evaluated samples, zero safety-mask violations, an acceptable override rate and an offline evaluation pass; human approval always remains.
- No PPO, DQN or GNN-based RL. Evaluate GraphLinUCB against LinUCB without graph and a LinTS variant; if graph features do not help on held-out data, say so and keep the learner shadow-only. Synthetic or replay data is labelled and never described as real learned intelligence. No claim of a novel RL algorithm.
- MABWiser, Vowpal Wabbit and coba are studied, not copied. A license check precedes any incorporation, and `THIRD_PARTY.md` is updated only if a dependency is actually added.

### Alternatives Considered

Letting the learner override rules (rejected: unsafe); a deep RL policy (rejected: no data volume, hard to audit).

### Consequences

Policy decisions persist the feature vector, schema version and graph snapshot time so history is never recomputed. Spec and evaluation plan: `docs/GRAPH_SPEC.md`.

### Revisit When

Enough reviewed Change Guard decisions exist to evaluate graduation, or the evaluation shows the graph features add nothing.

---

## DEC-045 — Secrets and Credentials Handling

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

The project now uses live Profound, model-provider and (planned) Neo4j credentials, plus a Change Guard bearer token for an internet-facing endpoint.

### Decision

- One root `.env`, mode 600, git-ignored and never committed (checked 2026-10-03: `.env` is mode `-rw-------`, untracked and ignored; `.env.example` holds names only).
- Profound, model-provider and Neo4j credentials are supplied by the user; agents do not generate or commit them. Secrets are never printed, logged or placed in docs; smoke tools state they do not print keys.
- `NUXT_PUBLIC_*` variables are browser-visible and never carry secrets. The browser never reaches Neo4j or Profound directly.
- `CHANGE_GUARD_TOKEN` unset means `POST /api/change-checks` answers 503 (secure by default); comparison is constant-time and the token is never logged.

### Alternatives Considered

Per-service env files (more drift); committing an encrypted env (key management overhead for a hackathon).

### Consequences

Anyone reproducing the project supplies their own keys; features degrade to documented states without them.

### Revisit When

The service is deployed beyond a trusted network (move to a secret manager and real authentication, see DEC-039).

---

## DEC-046 — UI Work Split Between the Claude Session and the Gemini Session

**Status:** ACCEPTED (record of practice)  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

Two coding sessions worked in this repository at the same time: this Claude session (backend, Change Guard, docs, verification) and a Gemini session that worked on the Nuxt/Tailwind UI, including the 3D change topology committed in `ac84d54`.

### Decision

Lanes were separated by file ownership: Gemini on `frontend/**` presentation; Claude on backend, API contracts, tests and docs. hacp (explicit ownership plus bilateral contracts) was used to coordinate. Facts at this date: peer "b" never answered the hacp messages sent to it, and the Claude-side wiring contract `c-b4cd...` remains **proposed**, not accepted; the integration therefore rests on the committed code and the OpenAPI types, not on an agreed contract.

### Alternatives Considered

One session owning everything (slower); no coordination protocol (collision risk).

### Consequences

Frontend behaviour is verified only by its own tests (Vitest 48 reported, Playwright e2e); API/UI field-name drift is guarded by `frontend/tests/ui-contract.test.ts` and regenerated OpenAPI types, not by a negotiated contract.

### Revisit When

The peer responds, or the frontend is handed to a single owner.

---

## DEC-047 — Laya Prior + GraphLinUCB Shadow; Muse Data Not RL Training Fuel

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

Profound Lift needs a fast local decision layer and an online learner for control-plane actions without replacing Change Guard, PostgreSQL, Neo4j, or Profound. Meta Muse connector terms forbid using connector data for unrelated profiling or model training.

### Decision

1. **Laya** is a System-1 typed-decision prior (choice/score/noul), not the RL learner and not a chat model. It recommends; it never executes side effects.
2. **GraphLinUCB** (contextual bandit) is the control-policy learner, starting in **SHADOW** mode: baseline Change Guard decision is returned; Laya + bandit outputs are persisted for eval.
3. **Deterministic safety masks** always restrict eligible actions; the bandit cannot override masks.
4. **LLM escalation** runs only when rules and Laya cannot answer confidently (see `app/intelligence/escalation.py`).
5. **Muse connector payload must not** be written into global Laya/bandit/LLM training tables; authorized Muse context is allowed for the active request only.

### Alternatives Considered

End-to-end LLM for every decision; PPO/DQN on sparse rewards; training bandit on Muse conversation history.

### Consequences

`docs/AGENTMATCH_DECISION_LAYER.md` and `docs/MUSE_CONNECTOR.md` describe phases. OAuth multi-tenant Muse is phase 2. Laya weights remain optional until benchmarked.

### Revisit When

Shadow eval graduation criteria pass and ACTIVE mode is approved; or Muse OAuth V1 ships.

---

## DEC-048 — Production API Routing & Change-Check Read Semantics

**Status:** ACCEPTED  
**Date:** 2026-10-03  
**Supersedes:** None

### Context

Production Vercel builds embedded `http://localhost:8000` when `NUXT_PUBLIC_API_BASE_URL` was unset, causing browser loopback blocks. The experiments UI called `GET /api/change-checks`, which shared the agent bearer gate with POST and returned **503 CHANGE_GUARD_NOT_CONFIGURED** when `CHANGE_GUARD_TOKEN` was unset — not a Neo4j failure.

### Decision

1. **Canonical browser API base:** `NUXT_PUBLIC_API_BASE_URL` → `runtimeConfig.public.apiBaseUrl`; all HTTP/SSE via `useApiBase()` / `apiFetch()`. Vercel Production build fails if unset; client plugin rejects loopback in production bundles.
2. **Change checks:** POST `/api/change-checks` still requires `CHANGE_GUARD_TOKEN`. GET list is PostgreSQL read-only, requires `org_id`, no agent token (UI control plane).
3. **409 on experiment verify:** Preserved when verification window closed; UI disables verify using `verification.isOpen` and shows `eligibleAt`.

### Consequences

Muse/API submission uses the Cloudflare public hostname. Named tunnel migration remains operational follow-up (avoid long-lived `trycloudflare.com` for Meta review).

### Revisit When

Stable named tunnel hostname is provisioned and Vercel Production env is updated + redeployed.
