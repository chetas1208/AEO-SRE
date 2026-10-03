# B2 handoff: Profound + public evidence + investigation intelligence

Scope: backend deep campaign, B2. No schema/migration changes (requests in `docs/notes/schema-requests.md`, section B2). Nothing was run against live
Profound (no key); every Profound behaviour is covered by the existing respx contract fixtures (synthetic, spec-derived) plus `tests/unit/test_b2_profound.py`.

## Cutover (Profound key arrives)
1. Set `PROFOUND_API_KEY` in the root `.env`, restart API + worker. Nothing else.
2. `make profound-smoke` (`app/devtools/profound_smoke.py`): validates config, ONE read-only request (`GET /v1/org/categories`) normalized by the
   connector, then a row-limited capability probe (`--minimal` skips it). Prints a redacted JSON report + health state. Exit codes: 0 CONNECTED,
   1 DEGRADED, 2 NOT_CONFIGURED, 3 AUTH_FAILED, 4 RATE_LIMITED. Never writes, never runs agents.
3. `make ingest-live` calls `ingestion.run_scheduled_ingest`, the same function `pipeline.ingest` (worker job `ingest_profound_signals`) calls.
4. `/api/capabilities` -> `profound.meta.health_state` is one of NOT_CONFIGURED | CONNECTED | AUTH_FAILED | DEGRADED | RATE_LIMITED (+ `source_mode`).
   Unconfigured is reported there once; nothing is logged per poll/ingest. NOTE: `overall` still goes `degraded` while Profound is unavailable
   (pre-existing B5 semantics; I did not change it).

## Profound (items 1-8)
- Shapes stay in `connectors/profound` (normalize.py returns dataclasses; the domain reads `Signal` rows). New: `health.py` (derive_health).
- **Provenance**: every ingested `Signal.raw` carries `source_mode` (LIVE | REPLAY | FIXTURE | TEST) and `ingestion_run_id`. `ingest_org(..., source_mode=...)`
  is declared by the caller (default LIVE), never inferred from org name/domain. `domain/source_mode.py::source_mode_for_signal(source, raw)` reads the
  persisted value first and only falls back to the legacy `source` label for old rows. A failing live pull records `unavailable/failed` and writes
  NOTHING (tested: `test_live_failure_never_falls_back_to_fixtures`); no code path calls the fixture seeder.
- **Runs + checkpoints**: Setting `profound.runs.<org>` (last 30 runs, newest last; `list_ingestion_runs`) with id, provider, source_mode, stage,
  started_at, completed_at, status, records_seen/normalized/created/updated, error_count, checkpoint, window, error. Per-surface checkpoints
  (`profound.last_sync.<org>.checkpoints`) advance only for surfaces that finished without errors; the next window starts at the oldest surface
  checkpoint minus the restate window, so a failed surface re-pulls its gap. Idempotent upserts unchanged.
- **Two-stage fetch**: stage 1 `IngestConfig.monitoring()` = prompts, visibility(+share of voice, avg position), competitors, citations, prompt volume
  (scheduled). Stage 2 `ingest_deep()` = query fanouts + FactCheck, triggered from `pipeline.stage_fanout` only when an incident is being investigated and
  a key is configured; full answers/citation details stay in `investigation/answers.py` (already stage 2). `ingest_org` default (`full`) is unchanged for
  back-compat/tests.
- **Prompt clusters** (`app/incidents/clustering.py`): hard constraint (normalized topic, language) then embedding similarity (default = deterministic
  lexical hashing embedder, id `hashing-v1-256`; honest label: NOT semantic; swap via the `Embedder` protocol). Existing assignments never move
  (`reassign=True` is the only way; every move is in `changes`), removed prompts are `retired` not dropped, an unchanged membership produces no new version.
  Versions in Settings `clusters.current/history.<org>`; written from ingestion (non-fatal).
- **Multi-signal correlation + signature** (`incidents/signature.py`, `detector.py`): visibility regression + citation loss (citation_share, cited/lost sources)
  + competitor gain in one cluster/scope = ONE incident (`LOST_CITATION_SOURCE` joined the `displacement` family); a material query-fanout shift in the cluster
  corroborates (`context.fanout_shifts`, `correlated_signals`) but never raises an incident alone; corroboration adds <=0.05 confidence per extra signal
  family. `context.signature` = {incident_type, family, topic, cluster_id, platform, persona, competitor(s), primary_metric, direction, metrics, signals,
  signature_key}. Platform/persona are set only when EVERY primary anomaly is in that segment (`profound:model=X` / `profound:persona=Y` sources);
  segment-only regressions are their own incidents and fold into a cluster-wide incident only when a headline anomaly of the same family exists.
  Dedup (`filter_duplicates`) is by (family, cluster, platform, persona). `dedup_key` shape is unchanged. B1's fingerprint reads this signature.

## Public web (item 9)
- SSRF: existing guard kept (scheme/port/credentials/internal suffixes/DNS-resolved non-public incl. link-local metadata, ULA, v4-mapped, 6to4; every
  redirect hop re-validated, caps on bytes/redirects/timeouts, cache). Added: integer/hex numeric host spellings rejected. KNOWN GAP: validation resolves DNS
  once and httpx resolves again at connect (DNS-rebinding TOCTOU); closing it needs an IP-pinning transport.
- Extraction drops cookie/consent/newsletter/modal/breadcrumb elements (id/class/role/aria-label), nav/footer/aside, and short cookie/legal/footer blocks;
  headings are kept. `FetchResult.normalized_hash` = hash of non-boilerplate blocks; `diff_snapshots` reports `unchanged` when only boilerplate differs
  (also for stored Evidence rows via `raw.normalized_hash`) and ignores boilerplate blocks in section diffs. `content_hash` semantics unchanged.
- `connectors/web/source.py`: `SourceCategory` OWNED | COMPETITOR | THIRD_PARTY | COMMUNITY | UNKNOWN (categorical; no numeric authority). Evidence rows keep
  `raw.source_category`, `raw.incident_relation` (cited_by_ai_and_on_topic | owned/competitor/third_party_page_on_topic | fetched_no_relevant_content),
  `raw.normalized_hash`, plus existing source/url/retrieved_at/excerpt/content_hash/retrieval_method. `EvidenceType` enum untouched.
- Graph (`pipeline.stage_graph`, `evidence/provenance.extract_for`): every edge has provenance; `provenance.hash` always hashes the stored EXTRACT (the page
  hash is kept as `source_hash`); `extract_kind` = excerpt | title_fallback | none (a title is never article evidence); a hypothesis gets SUPPORTS only from
  evidence that has a quoted excerpt and is not the bare metric-change symptom (those and title-only items become ASSOCIATED_WITH); a hypothesis citing no
  evidence has only ASSOCIATED_WITH; no node is left disconnected (tested).

## Investigation (item 10)
New modules in `app/investigation/`: `taxonomy.py` (RootCause enum + rule/layer mapping), `control.py`, `counterevidence.py`, `confidence.py`,
`assessment.py`, and `budget.py` extensions. `pipeline.stage_gate` is now two-stage: the unchanged deterministic evidence gate, THEN `assess_investigation`.
A hypothesis becomes `confirmed` only if both pass; nothing reads a model verdict.
- **Taxonomy**: competitor_canonical_content_improved, owned_content_stale/buried/missing, third_party_misinformation, citation_source_shift,
  query_interpretation_shift, canonical_fact_changed, model_variance, insufficient_evidence. Existing RCA rules map onto it. GAPS: no RCA rule yet
  emits `owned_content_missing`, `canonical_fact_changed` or `model_variance` as a hypothesis (they exist as taxonomy values; model_variance appears as an
  `alternatives` entry when controls say category-wide). Adding those rules is the next step.
- **Competing hypotheses**: all gate candidates are ranked by evidence-derived confidence (`context.assessment.ranking`, `alternatives`); a near-tie
  (<0.03) between different causes blocks both ("ambiguous_between"); `insufficient_evidence` is a standing alternative when nothing confirms.
- **Counterevidence** (`counterevidence.py`): cause-specific checks (contradiction scan, own page changed, competitor page not new, category-wide movement,
  fanout shift, ...) each `found | clear | not_checkable`; the search counts as performed only with >=2 checkable checks, otherwise the hypothesis is
  blocked (`counterevidence_not_searched`). For the leading gate-confirmed hypothesis `reverify_urls` re-fetches (uncached) up to 2 supporting pages inside the
  web budget (`pipeline.counter_fetcher_factory`, overridable in tests); a vanished/changed page is counterevidence. A `found` check with strength >=0.7 blocks.
- **Control movement** (`control.py`): own adverse delta vs controls read from Signals (competitor share series, the brand's other clusters, other platforms for
  a platform-specific incident). >=60% of >=2 controls fell with us -> CATEGORY_WIDE: brand-specific causes are blocked and halved in confidence, model_variance
  is listed. <=25% -> BRAND_SPECIFIC. Fewer than 2 usable controls or mixed -> INCONCLUSIVE: allowed but confidence capped at 0.70 (never "high").
- **Confidence** (`confidence.py`): weights families .20, temporal .15, consistency .15, ranker support .30, source directness .15 (ordinal, explicitly not
  authority), prior .05 (LLM/rule confidence, the ONLY model-derived input) minus 0.5 x counterevidence penalty; caps for category-wide, unrun checks, and
  "high" (>=0.80) requires >=2 families + brand-specific control + counterevidence performed and clear. Confirm threshold 0.60. `Hypothesis.confidence` is
  overwritten with this value; the original prior is kept in `hypotheses_meta[hid].prior_confidence`.
- **Scope**: `context.scope` / `assessment.scope` states a platform/persona-specific regression applies to that scope only (`generalizes_beyond_scope: false`).
- **Budget/stop** (`budget.py`): `InvestigationBudget.from_settings` (web = max_web_sources, llm = max_model_calls, hypotheses = max_hypotheses, wall time =
  max_investigation_seconds, evidence cap 200), `BudgetTracker`, `decide_stop` -> confirmed | insufficient_after_budget | no_new_useful_evidence (same evidence
  fingerprint as the previous investigation) | insufficient_evidence. Persisted in `context.investigation.{stop_reason,budget,evidence_fingerprint}`.
  GAP: the investigation is still one linear pass (the stopping rules classify the outcome; there is no multi-round loop that fetches more when unconfirmed).
- **Model seam**: `rca.py` still talks to the existing `get_llm_client()`-style client behind `LLMClient.complete_json`; no provider SDK is imported by B2 code.
  B3 should swap that call site for ModelGateway.

## Tests (all Postgres)
`tests/unit/test_b2_profound.py` (provenance, no-fixture-fallback, health, runs, checkpoints/resume, stages, smoke, ingest-live parity),
`test_b2_correlation.py` (multi-signal incident, scope, dedup, clusters), `test_b2_web.py` (SSRF extras, redirects, boilerplate, source categories, evidence
fields, graph provenance), `test_b2_investigation.py` (taxonomy, control movement, counterevidence incl. reverify/budget, confidence features, LLM cannot
self-confirm, category-wide block, ambiguity, scope, budget/stop, stage_gate on real rows).

## Pre-existing failures not caused by B2 (seen during my runs)
`test_cutover_readiness::test_verification_refused_one_second_before_window` (ImmutableExperimentError, B1/B4 model guard) and approval/executed-state tests
(`test_full_loop_20_steps` step 14 `executing -> awaiting_verification`, `test_api_double_decision_is_409`): state-machine/approval changes from other agents in flight.
