# Handoff D3 (discovery gap: canonical truth vs what AI engines say)

Feature: for an org, compare ACTIVE canonical claims with AI-perceived claims from Profound and raise `DISCOVERY GAP: we state "X" but AI engines say "not-X"` as FACTUAL_CONFLICT incidents. No new routes, tabs, migrations or Muse dependency.
Tests: `cd backend && .venv/bin/python -m pytest tests/discovery_gap -q` -> 22 passed (Postgres and `AEO_TEST_DB=sqlite`); ruff clean. `tests/integration/test_worker_jobs.py` cron count 4 -> 5 (one-line edit). Neighbouring suites (changeguard_claims) still pass.

## Files
- `backend/app/discovery_gap/perception.py`: Profound adapter. `fetch_perception(client, category_id, brand_terms=, source_mode=)` (<=3 requests: up to 2 FactCheck-claims pages of 100, then 1 answers page of 50 only if FactCheck gave nothing). Pure `normalize_factcheck_claims`, `normalize_answer_claims` (markdown stripped; sentence must name the brand and parse to a known feature via G2's extractor). `PerceivedClaim` carries engines, prompt, occurrence, citation sources (domain + url), observed_at, window, source_mode, origin, confidence_cap.
- `analyzer.py`: groups identical perceived text, runs G2 `evaluate_canonical` (perceived = "proposed"), builds `DiscoveryGap` (canonical id/key/statement, perceived claim, relation, kind conflict|uncertain, confidence, engines, occurrence, citation domains, first/last observed, semantic_check, evidence_grade). Findings whose ids were not supplied are dropped. Answer-text-only gaps are always `uncertain` with confidence <= 0.5.
- `service.py`: `run_discovery_gap(session, org_id, ..., dry_run, source_mode)` and `run_job`. Flushes only; caller commits.
- `backend/app/devtools/discovery_gap_run.py`; `make discovery-gap ORG=<uuid> [DRY=1]`; worker job `detect_discovery_gaps` (`jobs.py`, `queue.py` JOB_KINDS) and `cron_discovery_gap` 03:40/15:40 UTC fan-out per org (`worker.py`).
- tests + `tests/discovery_gap/factcheck_claims_test.json` (SYNTHETIC, labelled; no real FactCheck claim shape has ever been observed).

## Run
`cd backend && .venv/bin/python -m app.devtools.discovery_gap_run --org <uuid> [--dry-run] [--category <profound category id>]` (real Profound key from env; category defaults to the ingestion-cached one, else domain match). Exit 0 ok / 1 honest skip / 2 failed.
Statuses: `ok`, `skipped_no_canonical_truth` (checked BEFORE any Profound call; creates nothing), `skipped_factcheck_unavailable` (exact reason text, e.g. `ValidationError: ... (HTTP 422) detail=...`, plus the answers-fallback state), `skipped_no_profound_category`, `skipped_profound_not_configured`, `skipped_rate_limited`.

## Persistence
- One incident per (canonical claim, perceived claim): `gap_key = sha256(org|canonical_id|normalized perceived)[:24]`; category `factual_conflict`, state `detected`, `context.signature` (incident_type/family `discovery_gap`, `sources=[gap_key]` so the fingerprint is per gap), `context.discovery_gap` (all gap fields incl. `source_mode`), priority via `compute_priority` (severity component 0.85 conflict / 0.5 uncertain, detection confidence, feasibility prior), audit `incident.detected`; created through the ORM so number/fingerprint listeners, the graph outbox and the state guard apply.
- Evidence: type `profound`, status `live`, excerpt = perceived claim, retrieval_method `profound.factcheck_claims` or `profound.answers`, `contradiction_score` = relation confidence only for FactCheck-derived conflicts, `raw` = both claims, relation/reasons, engines/platforms, prompts, occurrence, citation sources, window, Profound reasoning/evidence, `source_mode`, `evidence_grade`. Upserted by content_hash (no duplicates).
- Re-run: same gap -> `unchanged`; changed occurrence/engines/citations/confidence -> `updated` in place (+audit `discovery_gap.refreshed`); a DISMISSED incident for the same gap is not reopened (`suppressed_dismissed`); other terminal states allow a new incident.
- Incident detail already exposes `context` and evidence `raw`, so no route was added.
- No remediation, no hypotheses written by D3. Investigation is not auto-queued (see requests-d3.md: `_profound_evidence` would delete this evidence). When someone investigates, existing RCA rules apply; D3 adds no new rule.

## Real vs mocked
Real: Profound calls in live validation (read-only, 4 requests total across runs; 598/600 budget left), canonical/ incident/ evidence persistence code paths, G2 deterministic rules.
Mocked/simulated: every test (FakeProfound or respx; `source_mode` SIMULATED/TEST); the scratch end-to-end used TEST canonical claims and the synthetic FactCheck payload with `source_mode=TEST`. The model/ranker are not used in tests (rules only; `use_default_semantics=False`); degraded layers are reported (`no_ranker`, `no_gateway`).

## Live findings (Mixpanel org f0e7939a..., category 30222f94...)
- `POST /v2/reports/factcheck/claims` with `include=[reasoning,models,evidence,citation_sources]` AND `group_by=[prompt]` -> HTTP 422 ``citation_sources isn't available with group_by; request it on the flat (ungrouped) claim list``. Fixed: adapter requests the flat list. (Real API behaviour, not in A7's docs.)
- Flat FactCheck claims call: HTTP 200, FactCheck is set up on the category, empty envelope (zero claims) for 2026-09-19..2026-10-02. So no real FactCheck claim shape was observed.
- `POST /v2/prompts/answers` healthy: 4 brand-mentioning, extractor-readable claims (ChatGPT, Gemini, AI Mode, AI Overviews; with citation domains e.g. reddit.com, amplitude.com) -> fallback source `answer_text`, labelled lower confidence.
- `discovery_gap_run --dry-run` on this org: `skipped_no_canonical_truth` (org has no canonical claims), no Profound call, nothing created.
- Scratch DB `aeo_d3` (dropped afterwards; alembic head 0006): TEST org + TEST canonical "SAML SSO is available on the Enterprise plan." + synthetic payload `source_mode=TEST`: run1 created 1 incident (severity high, priority 59.3) + 1 evidence; runs 2 and 3 created 0 (unchanged 1); audit had incident.detected.

## Limitations
- Detection is as good as G2's English rules (known feature vocabulary; ambiguous related-subject pairs need ranker/model, otherwise reported as degraded with no gap).
- Answer-text claims are sentence extractions from marketing-style prose; many are about other products or not claim-shaped and are discarded; real conflicts there can be missed.
- Citation data is carried as evidence context only; D3 does not itself decide "citation source shift" or "owned content missing" hypotheses.
- FactCheck rows are not date-stamped; observed_at = end of the query window.
