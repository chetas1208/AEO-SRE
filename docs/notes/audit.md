# A1 Audit: workspace, tooling, doc tensions, Profound API, datasets

Audited 2026-10-02. Facts only; secrets never printed (set/unset only).

## 1. Tree inventory (at audit time)

- Git repo exists on `main`, 1 commit (`33265f2 chore: monorepo scaffold, shared enums, build brief`). Other agents already have untracked work: `backend/app/core/{audit,events,init_db}.py`, `backend/app/models/{core,evidence}.py`, `backend/app/workers/{jobs,queue,worker}.py`, `backend/tests/`, `frontend/` (package.json + node_modules).
- Committed scaffold: `Plan.md`, `UI.md`, `docs/BUILD_BRIEF.md`, `Makefile`, `docker-compose.yml`, `.env` / `.env.example`, `.gitignore`, `backend/pyproject.toml`, `backend/app/{core/config.py,core/db.py,domain/enums.py,models/__init__.py}`, empty package dirs (`api, connectors, evidence, experiments, incidents, interventions, investigation, learning, policy, schemas, services, workers`), `backend/ml/artifacts/.gitkeep`, `data/.gitkeep`, `docs/notes/.gitkeep`.
- `references/` (gitignored) already contains clones: `blackbox-datahub`, `campaignpilot`, `info-ninja`.
- `backend/aeo_sre_backend.egg-info/` exists and is NOT gitignored (add `*.egg-info/` to `.gitignore`; A14).
- No application logic exists yet; `pytest --co` collects 0 tests.
- Workspace path contains spaces (`Marketing Hackathon 3Oct`); quote paths in scripts. Compose project name is `marketinghackathon3oct`.

## 2. Tool availability

| Item | Status |
|---|---|
| uv | 0.12.17 |
| Python | venv `backend/.venv` = 3.12.3 (good); system python3 = 3.13.12 (do not use) |
| Node / pnpm / npm | v22.22.2 / 11.23.0 / 10.9.7 |
| git | 2.43.0 |
| Docker | 29.5.3, rootless. The CLI fails unless `DOCKER_HOST=unix:///tmp/xdg-1989081997/docker.sock` is exported (default `~/.docker/run/docker.sock` absent) |
| Compose services | `postgres` (pgvector/pgvector:pg16, healthy, 5432), `redis` (7-alpine, healthy, 6379; PING ok) |
| Postgres | DBs: `aeo`, `aeo_test` (already exists). Only extension installed: `plpgsql`. **pgvector extension NOT created** (image has it; A14 migration must `CREATE EXTENSION IF NOT EXISTS vector` if used) |
| gh | logged in as `chetas1208`, scopes `gist, read:org, repo`, https protocol |
| Network | github.com 200, pypi.org 200, registry.npmjs.org 200, huggingface.co 200, docs.tryprofound.com (308 then OK), api.tryprofound.com 200 |
| Venv packages | Installed incl. ml extras: fastapi 0.142.2, sqlalchemy 2.1.2, psycopg 3.3.6, asyncpg, arq 0.28.0, trafilatura 2.2.0, networkx 3.7, scikit-learn 1.9.1, lightgbm 4.7.0, sentence-transformers 6.1.0, datasets 5.0.1, torch 2.14.1, pandas 3.0.6, pytest 9.1.1, pytest-asyncio, respx, aiosqlite, ruff 0.16.10, sse-starlette. Not installed: `anthropic`/`openai` SDK, `profound` SDK, PyGithub (httpx is enough) |
| Hardware | 20 CPUs, 125 GB RAM, 2x RTX 3090 but **torch cannot use CUDA** (driver 12020 too old for this torch build) so train/embed on CPU. MiniLM `sentence-transformers/all-MiniLM-L6-v2` and `intfloat/e5-small-v2` are already in `~/.cache/huggingface/hub` |

`.env` key status (values never shown):

| Key | State |
|---|---|
| ENVIRONMENT, LOG_LEVEL, DATABASE_URL, REDIS_URL | set |
| MODEL_BASE_URL, MODEL_NAME, MODEL_API_PROTOCOL | set |
| **MODEL_API_KEY** | **unset** (LLM unavailable: rca/patch generation must degrade) |
| **PROFOUND_API_KEY** | **unset** |
| **PROFOUND_BASE_URL** | **unset** (public base URL is `https://api.tryprofound.com`; connector should default to it) |
| **GITHUB_TOKEN, GITHUB_OWNER, GITHUB_REPO** | **unset** (executor stays dry-run; `gh` is authed so lead could derive a token via `gh auth token` and pick a target repo; decision needed) |
| GITHUB_BASE_BRANCH, APP_BASE_URL, BACKEND_BASE_URL, NUXT_PUBLIC_API_BASE_URL | set |

Biggest operational risk: no Profound key and the API is Enterprise/on-request (section 4), so no live Profound signals unless the user supplies a key. Everything must degrade to `unavailable`, never fabricate.

## 3. Tensions between Plan.md, UI.md, BUILD_BRIEF and proposed resolutions

| # | Tension | Resolution |
|---|---|---|
| 1 | Repo layout: Plan section 5 has `apps/web`, `apps/api`, `aeo/`, `worker/`, root `migrations/`, `tests/`; Brief uses `frontend/`, `backend/app`, `backend/migrations`, `backend/tests`, `backend/ml` | Brief wins (it is the execution contract and Makefile matches it). README/ARCHITECTURE (A14) should state the mapping once. |
| 2 | Frontend scope: Plan section 6 = 3 plain-HTML routes, no CSS; UI.md has two layers: mockup A (10-item nav) / mockup B (3 nav) and a later Implementation Spec (3 tabs only, rich components, dark theme) | Take UI.md Implementation Spec (second half) as normative: 3 tabs (Incidents, Experiments, Settings), `/` redirects to `/incidents`. Functional wiring first, styling pass last (spec section 39 agrees with Plan). Mockup A extras (KPI row, right rail, Knowledge Graph, Learning pages) are out of scope. |
| 3 | Routes: Plan lists 3 routes; UI.md adds `/experiments/:id` and `/settings` | Build all five (`/incidents`, `/incidents/[id]`, `/experiments`, `/experiments/[id]`, `/settings`). Brief API already has `/api/experiments/{id}` and `/api/settings`. |
| 4 | Action names: Plan `update_page, new_canonical_page, faq, comparison_content`; enums `update_existing_page, create_canonical_page, create_faq, create_comparison_content` | Enums are single source (Brief). Plan/UI text names are display labels only; frontend maps enum -> label. |
| 5 | Experiment status: Plan `awaiting_reward`, `evaluated`, `failed`; enums `awaiting_verification, verified, rewarded, executing`... | Enums win. UI label "Awaiting Measurement" = `awaiting_verification`; README must say Plan's `awaiting_reward` == `awaiting_verification`. |
| 6 | Incident lifecycle: UI.md lists 10 states (Detected, Investigating, Needs Review, Ready for Action, Executing, Awaiting Measurement, Verified, Resolved, Dismissed, Failed); `IncidentState` has 17 | A12 exposes `state` (enum) plus a derived `display_state`; proposed map: detected/triaged->Detected; investigating/evidence_ready->Investigating; root_cause_proposed/root_cause_confirmed->Needs Review; intervention_proposed/awaiting_approval/approved->Ready for Action; executing/executed->Executing; awaiting_verification->Awaiting Measurement; verified/rewarded->Verified; closed->Resolved; dismissed, failed 1:1. Frontend must not invent states. |
| 7 | Severity: mockup A has 3 levels, mockup B and spec 4; Plan silent | 4 levels per `Severity` enum, computed in backend only (spec section 11). |
| 8 | Expected Outcome (+8-15pp, "High" success probability) appears in both mockups; Plan/Brief forbid invented numbers | Render only from evaluated experiments of same category with sample size n; else "Insufficient experiment history". Needs an API field (A12) such as `expected_outcome: {n, range}|null`. |
| 9 | Bandit "scores 0.71..." look like probabilities; UI section 22 calls 0.71 "Policy confidence" | Label as `score` (UCB/Thompson) and show `policy_probability` separately; do not call it confidence. |
| 10 | Mockup A lists "High enterprise intent 0.91" as a hypothesis | Context factor; belongs in `priority_breakdown`, not Hypotheses. |
| 11 | Identifiers: UI shows `#1042` and `EXP-1042`; Brief says `{id}` accepts UUID; routes `/incidents/:id` | API accepts UUID or integer `number`; frontend displays `#number`, `EXP-number`; canonical URLs use UUID (or number if A12 supports both; decide once). |
| 12 | UI.md section numbering: two documents concatenated in one file (sections 1-10 mockups, then 1-78 spec). Brief cites "UI.md section 51 interfaces" (= spec Frontend Data Contracts, camelCase TS). UI.md cites "Plan 6 route 3" and "Plan 4.8" which are correct | Treat "UI.md section N" in Brief as the Implementation Spec numbering. Wire format is snake_case (Brief) while spec TS interfaces are camelCase: frontend maps (already stated in Brief). Plan 4.x numbering itself is consistent (4.1 ingest ... 4.9 OBD); Plan section 8 uses a separate 1-15 engineering order, not 4.x. |
| 13 | LLM unavailable: Plan 4.4 says investigation "marked incomplete"; Brief says `generate_hypotheses` uses deterministic rules when LLM absent | Both hold: rules still produce `proposed` hypotheses; incident `investigation_status` = `partial` / `llm_unavailable`, and no LLM-written patch (Proposed Changes shows "LLM unavailable"). |
| 14 | Dev fixtures: UI.md section 72 allows "seed fixture for local dev"; Plan/Brief forbid demo data in app | Brief rule 1 wins: fixtures only in `backend/tests/fixtures`. No seed script in app/scripts. |
| 15 | Policy version format: UI `v0.3.1`/`v0.4.2`, Plan `policy_vN`, Brief `"v0.0.1"` | Brief format (semver string). |
| 16 | API contract gaps vs UI: UI needs global search (`/api/search`), global live feed for list/status/notifications (SSE), `range` filter, org switcher data, per-incident `/events` only in Brief | Add (A12): `GET /api/events` (global SSE heartbeat + incident created/updated) so Live pill and list auto-update; `range` query param on lists; `/api/search` optional/stretch. Live pill must bind to real SSE heartbeat. |
| 17 | Executors: Plan 4.8 Executor 2 = Profound Agent; Brief assigns only GitHub PR (A10) | Profound Agent executor is cut-order item; model it as `unavailable` capability. Note Profound agents API needs a pre-published agent and schema-defined inputs (section 4), so it is not a generic "apply remediation" call. |
| 18 | Demo claim "approve -> real GitHub PR" vs Brief "dry-run unless creds + approval" and `.env` GitHub vars unset | Lead decides target repo and token source; until then dry-run only and UI shows "dry-run". |
| 19 | Plan 4.1 promises "visibility, citation share, prompt volume, FactCheck, tracked prompts" from Profound as if one source; real API splits them (section 4); prompt volume is keyword-level on-the-fly, not per tracked prompt | A7 maps each signal to its actual endpoint; prompt demand for a cluster = keyword volume lookups (capped 1,000 distinct keywords/day) or absent -> priority `prompt_demand` marked unavailable. |
| 20 | pgvector in Plan stack vs not enabled in DB; Brief never uses embeddings in DB | Do not use pgvector unless needed; if A14 wants it, add extension in migration. |
| 21 | Plan 4.5 "FEVER 185k + VitaminC 450k+" vs actual HF sizes (section 5) | Use subsets; numbers in README should cite the HF row counts below. |

## 4. Profound API (verified from public docs and OpenAPI)

Sources: https://docs.tryprofound.com/llms.txt (index), https://docs.tryprofound.com/rest-api/introduction.md, `/rest-api/authentication.md`, `/rest-api/reports/reports-v2-overview.md`, `/cookbook/setup/endpoints-at-a-glance.md`, `/sdks/overview.md`. OpenAPI 3 (External API v0.60.2): https://registry.scalar.com/@profound/apis/external-api/latest/openapi.json?variant=processed (661 KB) and also served at https://api.tryprofound.com/openapi.json (both 200, no auth). Docs pages are fetchable as raw markdown by appending `.md`.

- Docs publicly reachable: yes (no login).
- **Access**: "only available upon request"; Enterprise plan customers. Keys created in app: Settings > Account > API Keys, expiring, shown once. We have no key.
- **Base URL**: `https://api.tryprofound.com`
- **Auth**: header `X-API-Key: <key>` (recommended). Spec also declares `BearerAuth` (JWT). Probes: no key -> `401 {"detail":"API Key missing in query parameters or headers"}`; bad key -> `401 {"detail":"Invalid API key"}` (so the API is live and responds JSON). Other errors: 403 insufficient permissions, 429.
- **Rate limit**: 600 requests/hour/key; headers `X-RateLimit-Limit/Remaining/Reset`; 429 carries `Retry-After`.
- **Dates**: `YYYY-MM-DD` interpreted in Eastern Time; data stored UTC, processed on EST schedule. **v2 `end_date` inclusive; v1 end exclusive.** Datetimes without `Z` are EST. Data is daily, so intraday detection is not possible.
- **Envelope**: `{info:{total_results,count,next_cursor,models,start_date,end_date,filter,...}, data:[...]}`. Pagination: `limit` 1-50 (default 10), `cursor`=`info.next_cursor`, optional `max_results`. Every v2 report has a `/stream` SSE variant. Filters: tree `and/or/not` + leaves `{field,op,value}`, max depth 3; ops `is,not_is,in,not_in,contains,matches,exists...`; values accept names or UUIDs; fields `model, topic, region, persona, prompt, tag` (+ entity-layer fields per report).
- SDKs: `pip install profound` (`from profound import Profound`; `client.organizations.categories.list()`, `client.reports.visibility(...)`); `npm i @profoundai/client`. Not installed in venv; httpx against the REST API is fine.

Scoping: everything is keyed by `category_id` (UUID) from `GET /v1/org/categories`. Org discovery: `GET /v1/org`, `/v1/org/{assets,domains,models,regions,personas}`, `/v1/org/categories/{id}/{assets,topics,tags,personas,regions,citation-categories,citation-tags}`.

Endpoint map for A7 (what we need):

| Need | Endpoint | Key fields |
|---|---|---|
| Visibility / share of voice / position (incl. competitors via `scope:"all"` or `assets` list) | `POST /v2/reports/visibility` | req: `category_id,start_date,end_date,group_by[date,model,topic,region,prompt,persona],metrics[visibility_score,share_of_voice,average_position],interval[day,week,month],scope[owned,all],assets,filter,sort,limit,cursor`; row: `asset{name,owned},rank,date,model,topic,region,prompt,persona,visibility_score,share_of_voice,average_position`. (v1 `/v1/reports/visibility` also has `mentions_count, executions`.) |
| Citations (domains/pages, owned vs competitor) | `POST /v2/reports/citations` | req: `entity[domain,page,citation_category],group_by[page,date,model,topic,region,persona,prompt],metrics[count,citation_share,rank,first_cited_at],scope[all,owned],filter(domain,page,analysis_type,citation_category,citation_tag)`; row: `domain,page,rank,date,model,...,count,citation_share,first_cited_at` (`first_cited_at` supports "new citation source" detection) |
| FactCheck accuracy | `POST /v2/reports/factcheck` | req: `category_id,start_date,end_date,group_by[date,model,region,persona,prompt,topic,tag,citation,theme],filter`; row: `accuracy,accurate,inaccurate` + dims. Requires FactCheck set up for the category (`POST /v1/reports/accuracy/factcheck-setup-status` exists). |
| FactCheck claims | `POST /v2/reports/factcheck/claims` | `include[theme,reasoning,models,evidence,citation_sources]`; row: `cluster_id,claim,occurrence,reasoning,evidence,citation_sources,accuracy,accurate,inaccurate,total_claims` |
| Raw answers (affected prompts, per-engine answers, mentions, cited URLs) | `POST /v2/prompts/answers` (+`/stream`; v1 `/v1/prompts/answers`) | `include[run_id,date,model,topic,topic_id,persona,region,tags,prompt,prompt_id,response,mentions,citations,citation_details,search_queries,analysis_types,sentiment_claims]`, `filter` incl. `domain`/`page` exact-URL |
| Tracked prompts | `GET /v1/org/categories/{id}/prompts` (also POST/PATCH/status) | filters `prompt_type, topic_id, tag_id, region_id, platform_id, persona_id, status`; cursor pagination |
| Sentiment | `POST /v2/reports/sentiment` (requires `asset`) | optional |
| Query fan-out | `POST /v2/reports/query-fanouts` | optional |
| Prompt volume | `POST /v2/prompt-volumes/volume/on-the-fly` and `/v2/prompt-volumes/intents/on-the-fly` | **keyword-level, not per tracked prompt.** req: `keyword,matching_type[exact_match,phrase_match],start_date,end_date,regions,platforms[chatgpt.com,gemini.google.com,perplexity.ai]`; row: `country_code,platform,matching_type,frequency,date,volume`; 1,000 distinct keywords per org per UTC day (429 above); slices with <=2 users omitted |
| Agents (Profound Agent executor) | `GET /v1/agents`, `GET /v1/agents/{id}` (schema.input/output), `POST /v1/agents/{id}/runs` body `{inputs:{...}}`, `GET /v1/agents/{id}/runs/{run_id}` (status `queued,running,succeeded,failed,cancelled,skipped,unknown`, outputs, steps); also `POST /v1/agents` create, `PATCH`, `POST /v1/agents/{id}/publish`, `/graph`, `/v1/agents/node-types`. Runs execute only the published version. | Not generic "remediate": needs an existing published agent whose input schema we fill. |
| Other | `/v1/content/{asset_id}/optimization` (+`/{content_id}`), knowledge bases, documents, projects/tasks, referrals/bots traffic (`/v2/reports/{referrals,bots}`, domain-scoped), `/v1/integrations`, activity logs | stretch only |

Implications for A7: build `ProfoundClient` against the spec with `X-API-Key` and default base URL; with no key, `capabilities()` returns `unavailable` for all and ingestion writes no Signals; respx fixtures (in tests only) can follow the field names above. Daily granularity and 24-48h lag mean baselines use daily points (`group_by:["date"]`). Re-download the spec for exact schemas: `curl -sL https://api.tryprofound.com/openapi.json -o openapi.json` (not saved in repo; A7 does not own a spec path, so keep it in scratch or `references/`). The API was never exercised with a valid key; response shapes come from the spec/docs, not live calls.

## 5. HuggingFace datasets (for A8)

All reachable without auth or gating. Verified via `https://huggingface.co/api/datasets/<id>` and `https://datasets-server.huggingface.co/size`.

| Dataset | Rows | Size | License | Notes |
|---|---|---|---|---|
| **tals/vitaminc** | 488,904 (train 370,653 / val 63,054 / test 55,197) | parquet 36.6 MB, original jsonl 255 MB (`train/dev/test.jsonl` in repo) | cc-by-sa-3.0 | No loading script. Fields: `unique_id, case_id, wiki_revision_id, label (SUPPORTS/REFUTES/NOT ENOUGH INFO), claim, evidence, page, revision_type (real/synthetic), FEVER_id`. Revision-derived, good for stale/changed-fact signal. Has NEI with real evidence text. Best primary source. |
| **copenlu/fever_gold_evidence** | 260,251 (train 228,277 / val 15,935 / test 16,039) | parquet 56 MB | cc-by-sa-3.0, gpl-3.0 | No script. Fields: `claim, label, evidence [[page, sent_id, text],...], id, verifiable, original_id`. Evidence text is included (original FEVER lacks it). First rows show SUPPORTS with evidence; NEI rows likely have empty/no gold evidence (not verified: A8 should check and mine negatives or use VitaminC for NEI). Best FEVER option. |
| fever/fever | n/a | n/a | cc-by-sa-3.0 + gpl-3.0 | Uses loading script (`fever.py`); dataset-viewer unsupported; claims only, evidence needs wiki-pages dump. Parquet conversion branch `refs/convert/parquet` exists. Avoid. |
| pminervini/hl-fever (alt) | 72,882 (train 59,550 / dev 13,332) | 6 MB | mit | Higher-level FEVER variant, small. |
| mwong/fever-evidence-related (alt) | 485,184 | 568 MB parquet | cc-by-sa-3.0, gpl-3.0 | Claim/evidence-relatedness pairs; large. |

Practical: `load_dataset("tals/vitaminc")` and `load_dataset("copenlu/fever_gold_evidence")` work as data-only parquet/jsonl; subsets of e.g. 20-50k per split are enough for a quick CPU run (MiniLM cached locally, no GPU). Raw data should go to the gitignored `backend/ml/datasets/raw/`. Cite licenses (CC BY-SA 3.0) in THIRD_PARTY.md. Plan's "185k FEVER" ~ original FEVER train; the gold-evidence variant has 228k train rows.

## 6. Other observations for the lead

- `.gitignore` lacks `*.egg-info/` and `backend/tests/**/__pycache__` is covered by `__pycache__/`.
- `app.core.config.Settings.profound_base_url` default is empty; set default to `https://api.tryprofound.com` (A14 owns config.py).
- `DATABASE_URL` uses `postgresql+psycopg` (psycopg3 async works); asyncpg is also installed; keep one driver.
- Makefile targets reference `app.api.main:app` and `app.workers.worker.WorkerSettings` (consistent with the Brief; worker.py already exists).
- No LLM key and no Profound key: end-to-end "live incident" requires the user to provide `PROFOUND_API_KEY` (Enterprise access) and optionally `MODEL_API_KEY`; otherwise the live demo can only show unavailable states plus web-collector evidence.
