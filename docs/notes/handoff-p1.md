# Handoff P1: live Profound + model cutover (2026-10-03)

No secret value appears here or in any log (checked: API log and worker log contain neither the Profound/model key nor an 8-char prefix of either).

## Verdict
- **Profound: LIVE, connection CONNECTED** (`make profound-smoke` exit 0; all 7 probed surfaces live_verified; `competitors` stays `degraded/no_competitor_assets_tracked` and is shown as such, see 1). Passive `/api/health.profound_state` = READY.
- **Model: LIVE** (`anthropic_messages`, fast tier `claude-haiku-4-5`, deep `claude-sonnet-4-6` per root `.env`; `make model-smoke` READY; real structured hypothesis calls made inside 4 live investigations).

## 1. Why `profound_smoke` said DEGRADED (real cause, not masked)
Two causes:
1. The probe always used `categories[0]`, which for this account is the empty personal category "Chetas Nikunjbhai Parekh" (no assets, no topics); every probe there was vacuous. Now `_pick_probe_category` (capabilities.py) picks the first category with an OWNED asset (smoke prints `probe_category`).
2. The `competitors` surface is genuinely `degraded` (`no_competitor_assets_tracked`): none of the account's categories track a non-owned asset. That is an account-configuration gap, not a connection fault. `health.py` now has `ACCOUNT_CONFIG_REASONS` / `counts_as_healthy`: the surface keeps its `degraded` state and reason everywhere (smoke, `/api/system/capabilities` detail, ingest report), but it no longer drags the connector verdict, `/api/system/capabilities` state, or the ingest run status to DEGRADED. Any other competitors failure still degrades.
Also fixed: `SurfaceOutcome.finalize` overwrote the specific reason with `no_rows_returned`; and passive `/api/health.profound_state` was DEGRADED forever because the dev fixture org (auth0.com) can never map to a Profound category. Orgs whose last run error is `no_profound_category_owns_*` are now excluded (the run records that error).

Kept the uncommitted live-debug edits after review: `query_fanouts` metric `total_fanouts` (matches live API), `NormalizedSignal.source` capped at 128, `Signal.source` String(128).

## 2. Migration
Fresh scratch DB `aeo_p1`: `alembic upgrade head` then `alembic check` FAILED on `signals.source` VARCHAR(64)->128 (drift confirmed; dev DB had been hand-altered). Added `backend/migrations/versions/0003_signal_source_128.py` (alter column, reversible). Scratch: upgrade to 0003 then `alembic check` clean. Scratch DB dropped. Dev DB `aeo` is at 0003 (column was already 128, so no data change). Fixture integrity: fixture-org signals md5 `2d72073a...` and fixture incident md5 `eb38177e...` identical before/after; fixture experiment `ac179cbd...` still `awaiting_verification`, updated_at 2026-10-02T20:29:43Z, 0 observations, 0 rewards.

## 3. Live inventory and real org
Profound account org "Chetas Nikunjbhai Parekh (Hackathon)", 4 categories:
| category | owned asset | competitors | topics |
|---|---|---|---|
| Chetas Nikunjbhai Parekh | none | none | 0 (empty) |
| SF Hackathon Participant 29 - CPG | Polar Seltzer (polarseltzer.com) | none | 8 |
| SF Hackathon Participant 29 - SaaS | Mixpanel (mixpanel.com) | none | 8 |
| SF Hackathon Participant 29 - Travel | lake.com | none | 8 |
The category named after the user is empty, so the account's own brand has no data. A real org already existed from the earlier live-debug session and was kept: **Mixpanel / mixpanel.com**, id `f0e7939a-615a-4e4c-92d8-a1253d50f23a`, category SaaS `30222f94-d10d-46ec-b93d-5a7d2a1a7eb6`. **Needs user confirmation** that Mixpanel (SaaS, closest to the auth0 fixture domain and the Profound Lift thesis) is the intended brand.
Competitor domains `amplitude.com, heap.io` on that org are OPERATOR-supplied, not from Profound (Profound tracks no competitor assets here), so no Profound `competitor_share` series exists.

## 4. Ingestion (same service as the scheduler)
Org signals: 488, all `source_mode=LIVE`, all with `raw_payload_ref` (`profound://...`, payload files under `data/profound_raw/`), unique dedupe key + idempotency key, run id in `raw.ingestion_run_id`. Kinds: visibility 255, query_fanout 100, citation 90, prompt_volume 43. Window 2026-09-12..2026-10-02 (last complete ET day), monitoring stage = cheap fetch. Checkpoints stored per surface (prompts, citations, factcheck, visibility, prompt_volume, query_fanouts = 2026-10-02). Run records in `settings` key `profound.runs.<org>` / `profound.last_sync.<org>`.
Idempotency bug found and fixed: re-runs reported 12-32 "updated" rows because Profound re-serves identical citation aggregates differing in the last float digit. `_same()` now compares with a 1e-9 relative tolerance. Re-runs after the fix: runs `bed4cd1f-526f-4c71-a6fa-21fe76a7de54` and `9539a28e-4f87-4090-8423-78cfea27373b`: status ok, created 0, updated 0, unchanged 388 each (the other 100 fanout rows come from the deep stage). Provenance gap: `signals.provider_run_id` is NULL (run id lives in raw only); not changed.

## 5. Detection and investigation
`pipeline.detect` on the real signals: incidents #2..#7 exist (not threshold tuning). #2, #3 were created earlier (investigated before the model was available, `llm.available=false`, rule fallback hypotheses; they remain `awaiting_approval` and cannot be re-investigated in that state). Detect re-run: 2 duplicates skipped (`detector.duplicate_skipped`), 4 new created (#4..#7: visibility drops 84%->15%, 79%->11%, 54%->8%, 53%->11%), all investigated live by the restarted worker (public web evidence from mixpanel.com/amplitude.com with robots/sitemaps, plus the live model). Results #4..#7: state `awaiting_approval`, `context.llm.used=true`, `calls` populated (purpose HYPOTHESIS_GENERATION, provider anthropic, claude-haiku-4-5, prompt hypothesis_generator_v3, latency ~4-10 s, tokens, cost ~$0.005), `produced_by=llm:anthropic_messages:claude-haiku-4-5@hypothesis_generator_v3`, gate decisions persisted in `context.gate`, policy decision persisted (all `observe`, `selection_basis=rule_fallback`, cold start: the evidence gate / policy do not yet justify a content action). Every hypothesis evidence id resolves to an `evidence` row (0 unresolved across all incidents). Unknown/invented evidence ids and invented URLs are rejected: `tests/reliability/test_adv_model.py` 14 passed. Interventions are `pending` (6 pending approvals, 1 approved = fixture); nothing was approved or executed.
Caveat: a pure `observe` result means the gate did not find content evidence (`content_evidence` check false) for a content change; that is the honest outcome, not a failure.

## 6. Runtime
API (:8000) and arq worker restarted detached onto final code (logs `/tmp/aeo-api.log`, `/tmp/aeo-worker-p1.log`, start script `/tmp/p1_start.sh`). `/api/health`: database/redis healthy, `profound_state=READY`, `model_state=READY`. `/api/system/capabilities`: profound healthy (detail `no_competitor_assets_tracked`), workers healthy. `GET /api/incidents?org_id=f0e7939a-...` serves the real incidents.
Gotcha for operators: `pkill -f 'uvicorn app.api.main'` inside a tool shell kills the invoking shell too (pattern in its own command line); kill by pid.

## Files changed by P1
`app/connectors/profound/health.py`, `capabilities.py` (probe category, plus kept fanout metric), `normalize.py` (kept), `app/services/ingestion.py` (finalize reason, `_same`, status rule, unmapped-org error), `app/services/capabilities.py`, `app/api/routes/health.py`, `app/devtools/profound_smoke.py`, `app/models/core.py` (kept), `migrations/versions/0003_signal_source_128.py`; tests in `tests/unit/test_b2_profound.py` (3 new) and `tests/reliability/test_adv_observability.py` (1 new).

## LIVE VERIFIED vs still mocked
Live verified: categories, assets, topics, prompts, visibility (headline + model/region/persona/topic), citations, factcheck (empty envelope), prompt volume, query fanouts, agents list; ingestion idempotency; detection on real data; live investigation incl. model structured output.
Not live verified / mocked: competitor series (account tracks none), FactCheck rows (envelope only), `/v2/prompts/answers` and sentiment (unused), approval -> experiment -> verify -> reward loop on real data (human approval required; verification needs the 24-48 h window), unmapped fixture org Profound path (by design never live).

## Blockers / needs the user
1. Confirm the brand/category (Mixpanel/SaaS vs Polar Seltzer vs lake.com; the account-named category is empty).
2. Optionally track competitor assets in Profound if competitor share-of-voice series are wanted.
3. Approve/reject real interventions in the UI (#2..#7 are awaiting approval, all `observe`).
