# Profound Lift — Build Plan

**An incident-response and reinforcement-learning control plane for AI discovery.**

> Profound Lift continuously detects commercially important failures in how AI systems represent a brand, investigates the root cause across Profound and the public web, proposes the safest remediation, tracks the intervention as an experiment, and learns which remediation works for which type of incident.

Positioning: **Marketing SRE for AI discovery.** Sentry / Datadog / PagerDuty, but for AI answer engines.

---

## 1. Thesis and scope

### Why this, not something else
Profound already ships: visibility monitoring, citations, prompt volumes, sentiment, FactCheck, source attribution, AI investigation, Agents, CMS publishing, human approval, content-gap and remediation workflows. Rebuilding any of that is a mistake.

The missing layer we attack:

> Which incident deserves intervention, what caused the underlying change, which intervention should we choose, and what have we learned from previous interventions?

### Core move
Turn raw observations (visibility -7.3%, citation share +2.1%, competitor +12%, new citation source, prompt volume spike...) into **incidents**: evidence-backed, prioritized, investigated, remediated, verified.

### Explicit non-goals
Do NOT build: generic content generator, generic SEO agent, multi-persona agent swarm, chatbot, marketing dashboard, fake attribution, revenue predictor, fake causal model, image/social generation, CRM clone, giant knowledge-graph UI, complex Nuxt design, custom LLM, PPO, multi-node infra, Kubernetes, Temporal, Kafka, 12 microservices.

### Hard rules
1. **No demo mode.** No `/demo` route, no hardcoded incident, no fake charts, no prerecorded JSON, no `if DEMO_MODE`. Demo the actual live product. Recorded payloads allowed in tests only, never presented as live results.
2. **Never fabricate.** Profound down → mark signals unavailable. Crawl fails → evidence `unavailable`, never infer content. No post-intervention data → `awaiting_reward`, never fake success.
3. **Doing nothing is a valid action** (`observe`).
4. **Measured reward only.** No LLM-as-judge, no vibes. Components stored separately.
5. **No invented money.** Intervention Priority score, not "$2.7M opportunity".
6. **Human approval before any execution.**
7. **Every policy update is versioned.** No silent mutation.
8. **Do not claim** the policy was trained on ZOZO / Open Bandit data. OBD validates machinery only.

---

## 2. The loop

```
Profound signals ─┐
Public web ───────┤
                  ▼
        SIGNAL INGEST → INCIDENT DETECTOR → IMPACT EVALUATOR (ignore / act)
                  ▼
        INVESTIGATOR (evidence DAG) → ROOT-CAUSE HYPOTHESES
                  ▼
        EvidenceRanker (ML) → INTERVENTION POLICY (contextual bandit)
                  ▼
        HUMAN APPROVAL → EXPERIMENT ACTIVATION → EXECUTION (Manual by default; optional adapters)
                  ▼
        VERIFICATION (delayed observations) → REWARD → POLICY UPDATE (new version)
                  └──────────────────────────► next incident
```

Profound tracked prompts update on a daily cadence; new prompts take ~24–48h to accumulate data. The system is designed around **delayed rewards**.

---

## 3. Domain model (durable objects, not "agents")

| Object | Meaning |
|---|---|
| `Signal` | Raw measurement received (raw payload + normalized record) |
| `Incident` | Meaningful anomaly requiring investigation |
| `Evidence` | Source supporting/refuting a hypothesis |
| `Hypothesis` | Possible root cause, with confidence |
| `Intervention` | Candidate action |
| `Approval` | Human authorization |
| `Experiment` | Executed intervention with before-state |
| `Observation` | Post-intervention measurement |
| `Reward` | Measured outcome, components stored separately |
| `PolicyVersion` | Bandit state at time of decision |

### Experiment ledger (most important table)
```
experiments
-----------
id, incident_id, policy_version, context_vector,
selected_action, alternative_actions, policy_probability, reason,
evidence_snapshot, proposed_patch, approval, executor, execution_reference,
before_metrics, after_metrics, reward, confidence,
created_at, executed_at, evaluated_at
```
`policy_probability` is stored so proper **off-policy evaluation** (IPS / DR) is possible later.

Experiment states: `proposed → approved → executed → awaiting_reward → evaluated` (+ `rejected`, `failed`).

---

## 4. Components

### 4.1 Signal ingestion
- **Profound connector** (observability substrate): visibility, citation share, prompt volume, competitor comparisons, model/region/persona segmentation, FactCheck, citations, tracked prompts (visibility, share of voice, avg position, executions). Persist raw payloads unchanged + normalized records.
- **Public-web collector:** official site, docs, pricing, sitemaps, press/release notes, GitHub releases, competitor sites, URLs Profound reports as cited. httpx + BeautifulSoup/Trafilatura. Content hash + snapshot per fetch.
- Org onboarding: `POST /organizations {"domain": "company.com"}` starts crawling. Any company, no special-casing.

### 4.2 Incident engine (no LLM)
Change thresholds + rolling baseline + severity aggregation over prompt clusters. Incident = clustered anomaly (e.g. "Enterprise SSO visibility 61% → 37%, competitor 21% → 54%").

### 4.3 Impact / priority model (transparent)
```
Priority = Prompt Demand × Buyer Intent × Incident Severity × Persona Importance
         × Competitive Displacement × Evidence Confidence × Remediation Feasibility
```
Normalize 0–100. Expose every component in UI. Below threshold → ignore / observe.

### 4.4 Investigation engine
Per incident, build an **evidence DAG** (NetworkX): visibility loss → affected prompt cluster → competitor gained → competitor page changed → citations added/removed → canonical content exists? → external sources missing/stale.
Every edge stores: `source, timestamp, confidence, extract, hash, retrieval_method`.
LLM reasons **over the evidence** to produce hypotheses (with confidence); it never invents causes. LLM down → investigation marked incomplete, rest of pipeline continues. Low root-cause confidence → recommend observe / human investigation.

### 4.5 EvidenceRanker (small real ML)
Purpose: given a claim + source passage → support / contradiction / insufficient + freshness risk, usable for explaining incidents. **Not** competing with Profound FactCheck; it lives inside our investigation engine.
- Data: **FEVER** (185k claims) + **VitaminC** (450k+ claim-evidence pairs, revision-derived → stale/changed info). Use subsets.
- Pipeline: MiniLM embeddings → engineered features → LightGBM / logistic regression.
- Features: embedding cosine, entity overlap, numeric mismatch, date mismatch, negation mismatch, token overlap, source age, URL ownership, title similarity, revision distance.
- Output: `{support, contradiction, insufficient, freshness_risk}`.
- Train on the day. Save real artifact + held-out metrics in `ml/artifacts/`.

### 4.6 Intervention policy (contextual bandit)
- **LinUCB** or **Thompson Sampling**. No PPO.
- Actions (fixed set, LLM cannot invent more): `observe`, `update_page`, `new_canonical_page`, `faq`, `comparison_content`, `publisher_outreach`, `structured_data`.
- Context vector: visibility_delta, citation_delta, accuracy_delta, competitor_delta, prompt_volume, buyer_intent, source_authority, source_freshness, owned_source, third_party_source, content_exists, factual_conflict, persona_value, intervention_cost.
- **Cold start:** explicit configured priors (owned outdated page → `update_page`; third-party misinformation → `publisher_outreach`; missing canonical info → `new_canonical_page`; minor low-volume noise → `observe`). UI shows "cold-start policy" when history is insufficient.
- Every update → new `policy_vN`. Every experiment records which version chose it.
- LLM only writes the patch for the chosen action.

### 4.7 Reward
```
reward = 0.35·norm_visibility_delta + 0.30·citation_delta
       + 0.20·accuracy_delta + 0.15·competitive_delta
       − action_cost − risk_penalty
```
Weights are config, components stored separately, computed only from post-intervention Profound observations. Delayed-reward worker updates policy only after measured outcomes.

### 4.8 Intervention approval + experiment activation
Profound Lift is a closed-loop learning system: signal → incident → importance → investigation → evidence → intervention selection → human approval → execute/recommend → observe outcome → reward → learn policy. **GitHub was never the thesis**; execution is an interface and the core app works completely with only the manual executor.

Flow: the policy recommends → the human understands WHY (evidence, root cause, exact proposed change/diff, risk, alternatives + bandit scores) → **Approve / Modify / Reject** → an approved intervention **becomes an experiment** → the system records the exact action → verification begins.

Executors (`InterventionExecutor`: `execute(intervention, approval) -> ExecutionResult`, `name`, `capability()`):

| Executor | Status | Behavior |
|---|---|---|
| **ManualExecutor** (default, every action, always available) | built | No external mutation. Issues an **intervention package** (exact text/diff, target page, steps, evidence summary, risk, rollback, observation window) with status `awaiting_human_execution`. A human applies it and calls `POST /api/interventions/{id}/executed` (executed_at, reference_url, note, optional `actual_change` for deviations, flagged). That is a REAL execution (`dry_run=false`): the verification window starts at `executed_at` (+ Profound lag), the incident moves to `awaiting_verification`, and rewards/outcome ranges count it. `observe` needs no human step: activation starts observing. |
| **ProfoundAgentExecutor** | stub, `unavailable` | Capability reporting only; no Profound action endpoint is integrated. |
| **CMSExecutor** | not built | |
| **GitHubPRExecutor** | optional, secondary | Only when an operator explicitly selects it on approve/execute AND `GITHUB_*` is configured. Opens a PR, never merges. A GitHub `dry_run` preview never verifies or rewards. Unconfigured GitHub is a normal state, not an alarm. |

**Golden path:** incident → investigation → evidence gate → candidates → policy → human approve → manual package → human records executed → `awaiting_verification` → post-window Profound observation → reward → new policy version (`scripts/e2e_lifecycle.py`, `tests/integration/test_full_loop.py`).

### 4.9 Open Bandit validation (machinery only)
Use Open Bandit Dataset + Open Bandit Pipeline to validate LinUCB/TS implementation, benchmark, test IPS/DR estimators and delayed-reward plumbing. README states clearly: no domain transfer to AEO.

---

## 5. Architecture

Modular Python monolith + async workers. Postgres + Redis carry everything.

| Layer | Choice |
|---|---|
| API | FastAPI, Python 3.12+ |
| DB | PostgreSQL + pgvector, SQLAlchemy 2, Alembic |
| Queue | Redis + ARQ/Dramatiq |
| HTTP / crawl | httpx, BeautifulSoup / Trafilatura |
| ML | scikit-learn, LightGBM, sentence-transformers |
| RL | custom LinUCB/Thompson (+ OBP for evaluation) |
| Graph | NetworkX |
| Events | Server-Sent Events |
| Frontend | Nuxt 4 + TypeScript, pnpm |
| Runtime | Docker Compose (one-command boot) |

### Monorepo
```
aeo-sre/
├── apps/
│   ├── web/            # Nuxt 4: pages, components, composables, stores
│   └── api/            # FastAPI main.py
├── aeo/
│   ├── domain/         # incidents, signals, experiments, evidence, interventions
│   ├── connectors/     # profound, web, llm (github: optional executor)
│   ├── ingestion/ detection/ investigation/ evidence/ ranking/
│   └── policy/ planner/ executor/ verifier/ learning/
├── ml/                 # datasets, features, train_evidence_ranker.py, evaluate.py, artifacts/
├── worker/             # jobs/, worker.py
├── migrations/
├── tests/              # unit, integration, replay
├── scripts/  docker/  docker-compose.yml  pyproject.toml  pnpm-workspace.yaml  README.md
```

---

## 6. Frontend (wiring only, no CSS work)

Three routes, plain HTML:
1. **`/incidents`** — table: Priority, Incident, Topic, Status, Confidence, Detected.
2. **`/incidents/[id]`** — the product: summary, observed change, affected prompts, metrics, evidence, hypotheses, evidence graph as nested data, recommended + alternative interventions with bandit scores, Approve/Reject/Execute, live event log.
3. **`/experiments`** — Experiment, Action, Incident class, Before, After, Reward, Policy version, Status.

**SSE** `GET /api/incidents/{id}/events` streams real backend progress (fetching prompts, citation analysis, crawling competitor, contradiction score, hypotheses, policy computation).

---

## 7. Failure-safe modes

| Failure | Behavior |
|---|---|
| Profound unavailable | System stays alive; Profound signals marked unavailable; never fabricated |
| LLM unavailable | Detection, crawler, bandit continue; investigation marked incomplete |
| Page crawl fails | Evidence `unavailable`; never infer content |
| Bandit insufficient history | Transparent prior policy, labeled "cold-start" |
| No post-intervention data | Experiment `awaiting_reward`; never fake reward |
| Low root-cause confidence | Recommend `observe` / human investigation |

---

## 8. Engineering order

1. **Domain model + Postgres.** All objects in §3.
2. **Profound connector.** Real visibility/citation/prompt/competitor data; raw + normalized.
3. **Public-web evidence collector.** Official site, cited URLs, competitor pages; hashing + snapshots.
4. **Incident engine.** Thresholds + rolling baseline + severity aggregation.
5. **Investigation engine.** Evidence DAG, changed citations/pages, hypotheses.
6. **EvidenceRanker.** Train on FEVER/VitaminC subset, held-out eval, save artifact + metrics.
7. **Impact model.** Transparent priority scoring.
8. **Contextual-bandit policy.** LinUCB/TS, versioned, cold-start priors, `observe`.
9. **Intervention approval + experiment activation.** ManualExecutor + `record_manual_execution` first (default, no credentials); GitHub PR / Profound Agent / CMS are optional adapters behind the same interface.
10. **Experiment ledger + delayed reward worker.** Before/after snapshots; update only on measured outcomes.
11. **Nuxt wiring.** Three routes + SSE.
12. **Replay/integration tests.** Recorded payloads for tests only.
13. **Docker Compose + one-command boot.**
14. **README with exact scientific limits.**
15. **Run against a real public company/domain and leave it running.**

### Time-boxing (8h day, suggested)
| Block | Items |
|---|---|
| 0:00–1:00 | 1, scaffold, compose |
| 1:00–2:30 | 2, 3 (start EvidenceRanker training in background) |
| 2:30–4:00 | 4, 5, 7 |
| 4:00–5:00 | 6 finalize, 8 |
| 5:00–6:30 | 9, 10, 11 |
| 6:30–7:30 | 12, 13, 14, point at real domain |
| 7:30–8:00 | Demo rehearsal on live system |

Cut order if behind: optional executors (GitHub / Profound Agent) → OBP benchmark → LightGBM (use logistic) → experiments page polish. Never cut: ledger, versioned policy, `observe`, approval, no-fabrication rules.

---

## 9. Demo plan (4 minutes, live)

Event rules: 4-minute demos; all work built during the day. Demo the actual product.
1. (0:20) Problem: signals ≠ decisions. Show org already crawling.
2. (0:50) `/incidents` — real incident, priority breakdown.
3. (1:20) Incident detail — SSE log, evidence DAG, hypotheses, EvidenceRanker scores.
4. (0:50) Policy: bandit scores, cold-start label, `observe` alternative; approve → intervention package → human marks it executed (a GitHub PR only if that optional executor is selected).
5. (0:40) `/experiments` — ledger row in `awaiting_reward`; explain delayed reward + policy versioning.
6. Close: intervention-outcome dataset is the moat.

If asked "different company?" → `POST /organizations` live.

---

## 10. Scientific limits to state in README

- Cold-start priors are hand-set config, not learned.
- Bandit has no real AEO outcome data on day one; learning claims limited to the machinery and logged experiments.
- OBD/OBP used only to validate implementation and OPE estimators; no domain transfer.
- Attribution is observational; Profound data is daily, with 24–48h lag; confounders (model updates, competitor moves) exist. No causal claim.
- EvidenceRanker trained on Wikipedia-style claims (FEVER/VitaminC); web-page transfer is unvalidated beyond held-out metrics.
- Priority score is a ranking heuristic, not a revenue estimate.

---

## 11. Moat

Day 1: incident-response engine. 1,000 interventions: AEO intervention dataset. 10,000+ and many companies: policy conditioned on industry, brand maturity, engine, persona, prompt type, incident type, source structure, competitive environment.

The durable asset is the **intervention-outcome dataset**: `context → problem → evidence → action → result`.

---

## 12. README top-of-page copy

> **Profound Lift is an adaptive incident-response system for AI discovery.**
>
> Modern marketing teams can measure how brands appear across AI answer engines, but every visibility loss, citation shift, factual error and competitive displacement still creates the same questions: Does this matter? What caused it? What should we do? And did our intervention work?
>
> Profound Lift converts those signals into evidence-backed incidents, investigates their root causes, selects safe interventions using a learning policy, executes only after human approval, and measures the resulting outcome.
>
> Every intervention becomes an experiment. Every experiment improves the next decision.
