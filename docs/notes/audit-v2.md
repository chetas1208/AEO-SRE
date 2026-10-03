# Audit V2: M3-impl, M4, M5, M6, M7 (investigation / evidence gate / ML / bandit)

Date 2026-10-02. Evidence levels: 0 absent, 1 scaffolded, 2 unit, 3 integration, 4 live, 5 closed loop.
Postgres is authoritative (`aeo_test_<pid>` per run); SQLite via `AEO_TEST_DB=sqlite`.

## Verdicts

| Milestone | Verdict | Level | Notes |
|---|---|---|---|
| M3 implementation (code review only; live = V4) | IMPLEMENTED, live not assessed here | 2-3 | Profound client is exercised by unit/integration tests with mocks only. |
| M4 detection / priority | VERIFIED | 3 | Rolling median/MAD baseline, provided-baseline fallback, event metrics, per-cluster grouping, family dedup, absolute+relative+z noise floors, 7-component geometric-mean priority with per-component `source` (measured/default), no revenue claims (priority module states "not a revenue estimate"; test asserts no "revenue"/"$" in stored text). |
| M5 investigation / evidence DAG / gate | VERIFIED after one repair (P1 bug, SQLite only); two design findings recorded | 3 | See "Case B repair" and "Findings". |
| M6 EvidenceRanker | REAL and wired, weak by design | 3 | LightGBM on lexical+MiniLM-cosine features, trained on FEVER+VitaminC. 62% 3-class accuracy; freshness head unusable (AUC 0.50). |
| M7 bandit | VERIFIED | 3 (2 for learning-from-live-reward; needs V3's reward path for 5) | LinUCB + Thompson, immutable versions, decisions persisted, reproducible. |

## M4 details
- `detect_incidents` twice => one incident (`test_duplicate_detection_is_idempotent`, existing). Added:
  `test_later_equivalent_signal_does_not_create_second_incident` (extra signal, later `now`),
  `test_pipeline_detect_twice_creates_one_incident` (through `pipeline.detect`),
  `test_detection_is_deterministic_and_has_transparent_priority`.
- Dedup key is (category family, prompt cluster) against NON-terminal incidents. Limitation: dedup is a
  read-then-insert with no unique DB constraint, so two truly concurrent `detect` runs for the same org could both insert
  (worker uses one job per org; not exploitable from a single worker). A closed/dismissed incident does not block a new
  one for a recurrence (intentional).
- Suppression: absolute minimum, relative minimum, robust z-score floor; tests for stable series, small noise,
  org scoping, empty signals pass.

## M5 details
- Provenance: `Evidence` rows carry url, `retrieved_at`, `observed_at` (= page Last-Modified else fetch time),
  sha256 `content_hash` of normalised text (None for blank/failed), `retrieval_method`, excerpt, per-block hash index
  and diff in `raw`. Edges carry provenance (source/timestamp/confidence/extract/hash/retrieval_method) asserted in
  `test_full_loop_20_steps`. Experiments freeze `evidence_snapshot` (SNAPSHOT_VERSION 1).
- Gate (`investigation/evidence_gate.py`, unchanged): profound signal, quantitative delta, grounded content, citation
  (category dependent), timestamp alignment, rationale, aggregate confidence >= 0.6, cited ids must exist, no strong
  contradiction. unavailable/failed/inference never count. Naive datetimes are normalised to UTC inside the gate.
- LLM hypothesis path: `rca._accept_llm` discards any hypothesis citing an unknown evidence id
  (`test_rca.py` asserts the "unknown evidence ids" warning); the gate independently rejects unknown cited ids.

### Case B repair: SQLite gate stage crashed (3 failing tests)
Root cause: `app/services/pipeline.py::_features_from` did `utcnow() - e.observed_at`; SQLite returns tz-naive datetimes,
so `TypeError: can't subtract offset-naive and offset-aware datetimes` aborted the whole `gate` step. Because the
step raises before commit, the confirmed-hypothesis status and `gate`/`gate_primary` context were lost, so the incident
came back "unconfirmed" with `gate: None`. Postgres returns aware values, so it passed there. The gate itself was
correct (it already normalises naive times); the fixture had sufficient evidence (case A ruled out, case C ruled out).
Fix: normalise `observed_at` to UTC before subtracting. Regression: `tests/unit/test_pipeline_naive_datetimes.py`.
The third failure (`...dry_run_preview...`) was the same root cause (state stuck before approval). A fourth, test-only
naive/aware subtraction in `test_full_loop_20_steps` was fixed with a `_utc()` helper.
Result: `AEO_TEST_DB=sqlite pytest tests/integration/test_full_loop.py` 4 passed (was 3 failed).
Also noted: a failing step losing already-mutated hypothesis state is by design (honest "unconfirmed"), but it hid the
error behind a quiet `pipeline.step_failed` log; consider surfacing failed gate step in the UI (V4).

### Findings: why the live e2e never confirms a root cause (recorded honestly; gate NOT loosened)
1. First crawl, no competitor diff. RCA rules only fire on observed change: `competitor_canonical_improved` needs a
   competitor page with status `changed`/`is_new`; `citation_source_changed` needs Profound citation changes;
   `owned_content_stale` needs a page-declared date or freshness_risk. A first crawl has no prior snapshot, so a
   competitor page is just `live`, no rule fires and only the non-actionable `no_actionable_cause` is produced
   (nothing reaches the gate; policy falls back to `observe`). Reproduced in a throwaway test (first crawl: 4 evidence
   rows, zero actionable hypotheses). This is correct: a page seen once is not evidence it is new or changed. A second
   crawl after real change (or real Profound citation-change signals) is required. Requirements are reasonable.
2. Real-ranker mismatch (design gap, not fixed). `collector.resolve_context` passes the incident title+summary
   ("Enterprise SSO visibility dropped 61 -> 37 ...") as the CLAIM to the NLI-style ranker. A metric-drop sentence is not
   something a web page can "support": with a changed rival page and the trained ranker, support scores are 0.014-0.024
   (label `insufficient`), so the gate fails `content_evidence` and `timestamp_alignment` ("no content evidence to
   align"). With the test `FixtureRanker` (keyword scorer, 0.9) the gate confirms. So `test_gate_confirms_natively`
   proves the gate wiring, not that the trained ranker can confirm. Recommended fix (needs product decision):
   build a testable claim per hypothesis/layer (e.g. "<competitor> offers <topic>"), not the incident headline.
3. Timestamp alignment uses `observed_at = Last-Modified or fetch time`. A page with no Last-Modified fetched more than
   24 h after the incident's `first_observed_at` is misaligned (fetch time says nothing about when the page changed).
   For drops that began days ago this blocks confirmation unless pages carry modified dates. Honest, but will make live
   confirmation rare; revisit with a snapshot-diff timestamp (time the change was first seen) instead of fetch time.
4. Heuristic-fallback scores were stored as `support_score` with no marker, so they could satisfy the gate
   unlabelled. Repair: `collector._score_with` now persists `label, degraded, method, version, freshness_known` in
   `Evidence.raw["ranker"]` (regression test `test_collector_persists_ranker_provenance_and_degraded_flag`). The gate
   still accepts degraded heuristic support (uncalibrated lexical coverage on real fetched text); recommend requiring
   `degraded == False` for support in production if the trained model is expected to be present.

## M6 EvidenceRanker (real, verified)
- Code: `backend/ml/{datasets,features,training,evaluation,inference.py,heuristic.py}`; serving wrapper
  `backend/app/evidence/ranker.py`; train: `python -m ml.training.train_evidence_ranker` (args in manifest).
- Artifact (git-tracked, 6.5 MB): `backend/ml/artifacts/evidence_ranker/{cls_primary.txt,cls_lexical.txt,fresh_head.txt,
  calibration.json,manifest.json,metrics.json,metrics_reeval.json}`; `.gitignore` explicitly un-ignores the dir.
  Version `evidence-ranker-20261002-e9e4fb7c`.
- Model: LightGBM multiclass (support / contradiction / insufficient), variant `lex_cos` = 36 lexical features
  (token/bigram coverage, entity/number/comparator/year/negation/antonym features, title sim, time-sensitivity) plus
  MiniLM-L6 cosine; temperature calibration; separate lexical-only fallback; separate VitaminC-derived freshness head.
- Data: FEVER (copenlu gold evidence) + VitaminC; train 40k / val 6k / test 12k (class-balanced subsample of official
  test splits), seed 13. Test never used for selection (validation macro-F1).
- Metrics (held-out test n=12000): accuracy 0.6195, macro-F1 0.617, balanced acc 0.616, ECE 0.009, log-loss 0.83.
  Baselines: majority 0.367 acc, LR overlap-only 0.458. Freshness head: AUC 0.502, accuracy 0.51 => `fresh_usable=false`;
  freshness_risk at serving is a heuristic (age/revision/time-sensitivity), flagged `freshness_known`.
- Limitations: Wikipedia-derived training pairs, web pages are out of distribution; 62% accuracy is weak; contradiction is
  often scored `insufficient` (e.g. "Eiffel Tower is in Berlin" -> insufficient 0.71); age/owned features are not learned.
- Executed here: deterministic inference twice => identical output; trained model returns
  `method=model:lex_cos, degraded=False`; `EvidenceRanker()` (no artifact) returns `heuristic, degraded=True, available=False`.
  Unit: `tests/unit/test_ranker.py`, `test_ranker_features.py` pass.
- Pipeline integration: `collect_evidence` -> `_load_ranker()` -> `Evidence.support_score/contradiction_score/
  insufficient_score/freshness_risk` + `raw["ranker"]`. New test `test_real_ranker_is_called_by_pipeline_and_scores_land_on_evidence`
  (real artifact, pass-through to HF cache) asserts model method/version and scores on rows.
- Packaging: `pyproject [tool.setuptools.packages.find] include = ["app*","ml*"]` is correct, BUT the installed
  editable finder/`top_level.txt` in the existing `.venv` only maps `app`: `import ml` works only with cwd=`backend/`
  (uvicorn/arq/pytest from backend are fine; a process started elsewhere silently serves the heuristic with
  `load_error="No module named 'ml'"`). Fix: re-run `pip install -e backend` (not done to avoid changing the shared venv).
  `lightgbm`/`sentence-transformers` are in the optional `ml` extra, so a deploy must install `.[ml]`.

## M7 bandit
- `app/policy/bandit.py`: disjoint LinUCB and Bayesian-linear Thompson (fixed common random numbers, so propensity is exact),
  epsilon mixture for full support, cold-start priors blended with weight n/(n+prior_strength), `rule_fallback` on error
  (prior argmax, else observe; probability 1.0 excluded from OPE by `selection_basis`). Vocabulary = `ActionType` enum,
  `observe` always in the allowed set. Features: fixed-order encoder in `app/policy/features.py`; priors are explicit data
  rules stored on every PolicyVersion.
- Versions: append-only rows, ORM raises `ImmutableVersionError` on update/delete, parent chain v0.0.1 -> v0.0.2.
  (DB-level raw SQL could still mutate; ORM-level guard only.)
- PolicyDecision persists context vector (+names/schema), policy_version_id, scores (mean/uncertainty/ucb/prior/probability
  per candidate), selected_action, probability, selection_basis, cold_start, n_related, allowed_actions, meta,
  created_at. When the evidence gate is unconfirmed the pipeline records `observe` as `rule_fallback` p=1.0 with
  `constrained_to_observe` (scores still visible).
- Reproducibility: new test `test_decision_reproducible_from_persisted_context_and_version` (linucb + thompson) recomputes
  scores and probabilities from the persisted version + context + allowed-action mask only and matches. The final random
  draw is seeded by `POLICY_SEED`/config seed; the distribution (what is persisted) is reproducible, the draw itself only when seeded.
- Migration: `policy_decisions`, `settings`, `policy_versions.source_experiment_id` (unique) are in
  `migrations/versions/0001_initial_schema.py` (lines ~72-106). Nothing to report to V1.

## Repairs made (files)
- `backend/app/services/pipeline.py` `_features_from`: tz normalisation (case B).
- `backend/app/investigation/collector.py` `_score_with`: persist ranker provenance and degraded flag.
- Tests added/changed: `tests/unit/test_pipeline_naive_datetimes.py` (new); `tests/integration/test_full_loop.py`
  (`_utc` helper, real-ranker test); `tests/integration/test_detection_db.py` (3 tests); `tests/unit/test_policy.py`
  (reproducibility); `tests/unit/test_ranker.py` (degraded provenance).

## Commands and results
See final section appended by the run (full-suite numbers).
