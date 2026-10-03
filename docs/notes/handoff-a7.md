# A7 handoff: Profound connector + ingestion

## Status
Built against the **published OpenAPI spec (External API 0.60.2)** and docs. **No `PROFOUND_API_KEY` is set** (root `.env` and process env), so **nothing was verified live**: no live calls were made and there are no recorded fixtures. All fixtures in `backend/tests/fixtures/profound/` are labeled `_fixture.origin = "synthetic"` (built from the spec schemas), except `openapi_subset.json`, which is a mechanical extract of the real spec (request fields/enums, response row keys) and is used by a contract test.
44 tests in `backend/tests/unit/test_profound_client.py` pass (Postgres and `AEO_TEST_DB=sqlite`); ruff clean.

## Files (owned)
- `backend/app/connectors/profound/`: `client.py`, `requests.py` (pydantic request validation), `errors.py`, `normalize.py`, `capabilities.py`, `store.py`, `timeutil.py`, `__init__.py`
- `backend/app/services/ingestion.py`
- tests/fixtures as above

## Real endpoint table (from OpenAPI 0.60.2, `https://api.tryprofound.com`, header `X-API-Key`)
Spec also declares `BearerAuth` (JWT); connector uses `X-API-Key` only. Rate limit 600 req/h/key (`X-RateLimit-Limit/Remaining/Reset`; 429 + `Retry-After`). Dates `YYYY-MM-DD` Eastern Time; **v2 end_date inclusive**. v2 reports: `{info:{total_results,count,next_cursor,models,...}, data:[flat rows]}`, cursor pagination, `limit` max 50 (answers 200, claims 100). V1 reports are deprecated (sunset pending) with positional `dimensions/metrics` arrays, so **v2 is used**; v2 is labeled **Beta** in the changelog.

| Surface | Endpoint used | Request essentials | Row fields used | Verified |
|---|---|---|---|---|
| Discovery: categories | `GET /v1/org/categories` | none | `id,name,organization{id,name}` | docs/spec only |
| Owned asset + competitors | `GET /v1/org/categories/{id}/assets` | none | `id,name,website,alternate_domains,is_owned` | docs/spec only |
| Topics | `GET /v1/org/categories/{id}/topics` | none | `id,name,status` | docs/spec only |
| Tracked prompts | `GET /v1/org/categories/{id}/prompts` | `limit<=10000,cursor,status[],analysis_type[]` | `id,prompt,topic{id,name},status,regions,platforms,personas,tags,analysis_types` | docs/spec only |
| Visibility score / SoV / avg position, time series, model/region/persona/topic/prompt segmentation | `POST /v2/reports/visibility` | `category_id,start_date,end_date,group_by[date,model,topic,region,prompt,persona],metrics[visibility_score,share_of_voice,average_position],interval,scope[owned,all],assets,filter,limit<=50,cursor` | `asset{name,owned},date,model/topic/region/persona/prompt{id,name},visibility_score,share_of_voice,average_position` | docs/spec only |
| Competitors / leaderboard | same endpoint, `scope=all` + `assets=[names of non-owned assets]` | | rows with `asset.owned=false` | docs/spec only |
| Citations / citation share | `POST /v2/reports/citations` | `entity[domain,page,citation_category],group_by[page,date,model,topic,region,persona,prompt],metrics[count,citation_share,rank,first_cited_at],scope,limit<=50` | `domain,page,date,...,count,citation_share` | docs/spec only |
| FactCheck | `POST /v2/reports/factcheck` (+ `/factcheck/claims` modelled, not ingested) | `group_by[date,model,region,persona,prompt,topic,tag,citation,theme]` | `date,topic/...,accuracy,accurate,inaccurate` | docs/spec only; **needs FactCheck set up on the category** |
| Prompt volume | `POST /v2/prompt-volumes/volume/on-the-fly` | `keyword,matching_type[exact_match,phrase_match],start_date,end_date,regions,platforms[chatgpt.com,gemini.google.com,perplexity.ai]`; 1,000 distinct keywords/org/UTC day (429 above) | `country_code,platform,matching_type,frequency,date,volume` | docs/spec only; **keyword-level, not per tracked prompt** |
| Agents | `GET /v1/agents` (list only) | `limit` | `id,name,status` | docs/spec only; connector never runs agents |
| Not used | `/v2/prompts/answers`, `/v2/reports/sentiment`, `/query-fanouts`, `/stream` SSE variants, `/v1/agents/{id}/runs` (mutating) | | | |

## Public interfaces
- `from app.connectors.profound import ProfoundClient` (no-arg ctor reads `PROFOUND_API_KEY`/`PROFOUND_BASE_URL`; base URL defaults to `https://api.tryprofound.com`; A12's `services/capabilities.py` already uses this shape).
- `await client.capabilities() -> dict[str, CapabilityState]` over surfaces `visibility, citations, prompts, prompt_volume, competitors, factcheck, agents`; never raises. Shared 5-min TTL cache across instances (probes cost ~8 requests of the 600/h budget; volume probe uses 1 of 1,000 daily keyword lookups). `await client.capability_report()` returns `CapabilityStatus(state, verification, reason, endpoint, doc_confidence, checked_at)` per surface: `verification` is `live_verified` only when a probe just returned the documented shape, else `unverified`. No key -> every surface `unavailable/not_configured/unverified` with zero network.
- Errors: `ProfoundNotConfigured, ProfoundAuthError(401), ProfoundPermissionError(403), ProfoundNotFound, ProfoundValidationError(400/422, `.detail`), ProfoundRateLimited(`.retry_after`), ProfoundServerError, ProfoundTransportError, ProfoundResponseError`. Retries: 5xx/timeouts/transport with exponential backoff + jitter; 429 honours `Retry-After` (or `X-RateLimit-Reset`) up to `max_retry_after=60s`, longer waits raise immediately so callers degrade.
- Request models validated pre-send: `VisibilityQuery, CitationsQuery, FactCheckQuery, FactCheckClaimsQuery, VolumeQuery, PromptsListQuery`.
- `RawPayloadStore` protocol (`save/load`), `InMemoryRawPayloadStore`, `FileRawPayloadStore` (default `data/profound_raw/`, gitignored). Ref = `profound://<sha256[:32]>` goes in `Signal.raw_payload_ref`; each Signal's `raw["row"]` carries its source row unchanged.
- Normalizers return `NormalizedSignal` dataclasses (kind, metric, value, baseline=None, observed_at, prompt, prompt_id, cluster, engine, persona, region, competitor, raw_ref, segment, extra). Nothing Profound-shaped leaves the package.
- `await ingest_org(session, org_id, *, client=None, config=IngestConfig(), raw_store=None, now=None) -> IngestResult` (`.to_dict()` is JSON-safe for `Job.result`; fields `status ok|degraded|unavailable|failed, category, window, created/updated/unchanged, surfaces`). **Flushes only, caller commits.** A14: `pipeline.ingest` should call this and `.to_dict()`.

## Ingestion behaviour
- Category = the Profound category whose **owned asset's website/alternate domains match `Organization.domain`**. No match -> `unavailable` (`no_profound_category_owns_<domain>`), zero rows written.
- Window: last complete ET day (today's bucket is partial and would look like a false drop) back 21 days; incremental runs re-pull 3 days (Profound restates past numbers). Idempotent upsert keyed by sha256 of (kind, metric, observed_at, prompt, engine, cluster, persona, region, competitor, segment) stored in `Signal.raw["dedupe_key"]`; restated values are updated in place.
- Signals (detector vocabulary): `visibility`, `share_of_voice`, `avg_position`, `competitor_share` (`raw["competitor"]`), `citation_share`, `citation_count`, `accuracy`, `factual_conflicts` (= inaccurate count), `prompt_volume`. `baseline` is left null (Profound supplies none; the detector uses a rolling baseline).
- **Series separation**: segmented rows (model/region/persona/domain/prompt) get `Signal.source = "profound:model=ChatGPT"` etc., because the detector keys series on (metric, cluster, subject, source). Headline and per-topic series use `source="profound"`. Topic -> `PromptCluster` (created/updated from Profound topics + prompts; `prompts` JSON items `{id,text,status,regions,personas,platforms,tags}`).
- Writes `Setting` rows: `profound.last_sync.<org>` (`last_attempt`, `status`, `success{at,window}`; use `success.at` for "Last sync ... ago"), `profound.capabilities.<org>` (per-surface state/reason/rows/verification), `profound.category.<org>`.
- Fills `Organization.canonical_domains` / `competitor_domains` from Profound owned / non-owned assets **only if empty**.
- Failure handling: each surface/variant is independent; partial pages keep their rows and mark the surface `degraded`; 401/403/404 -> `unavailable`; everything else -> `degraded`. Never fabricates rows.
- Read-only: no agent runs, prompt creation or other mutations (test enforces).

## Unverified / assumptions (all marked `unverified` until a live key proves them)
1. Score scale (0-1 vs 0-100) and `accuracy` unit are not documented; passed through, detector auto-detects.
2. Whether grouped visibility rows keep `asset` populated; rows without `asset` are treated as owned. Competitor query passes an explicit name set to avoid mislabeling.
3. `citation_share` meaning (share of all citations vs share among owned scope) and whether multiple owned domains sum; kept per-domain (`source="profound:domain=x"`).
4. Prompt volume: keyword-level; we use **topic names** as keywords (`phrase_match`, max 8/run, 56-day window), which is our mapping, not a Profound concept. Weekly rows form the headline series; other frequencies get their own `source`.
5. v2 endpoints are Beta; FactCheck needs setup (403/422 expected otherwise -> `unavailable`/`degraded`).
6. Per-prompt visibility (`IngestConfig.prompt_level=True`) is off by default: row volume vs 50-row pages and 600 req/h.
7. SSE `/stream` variants not used (format unverified); pagination capped at `max_pages=10` per call.

## Requests for others
- A14: wire `pipeline.ingest` -> `ingest_org(...).to_dict()`, commit after. Add `data/profound_raw` to any volume mounts if workers are containerised.
- A12: `/api/system/capabilities` can expose `client.capability_report()` (has `reason`, `verification`) for the Settings screen, and read `Setting("profound.last_sync.<org>")` for the monitoring status.
- Once a key exists: run `ProfoundClient().capability_report(force=True)`, then record one trimmed/redacted sample per surface under `tests/fixtures/profound/recorded_*.json` and flip the unverified items above.
