# Handoff A3 (CampaignPilot / detector, priority, RCA)

Status: done. 71 unit tests pass (`cd backend && .venv/bin/python -m pytest tests/unit/test_detector.py tests/unit/test_priority.py tests/unit/test_rca.py`).
Ruff clean. No git operations performed. Reference clone: `references/campaignpilot` @ 0502103 (MIT, nothing copied).
Note: the shared Postgres `aeo_test` DB is also used by other agents' test runs; if a DB-backed test fails with
"relation does not exist", rerun or use `AEO_TEST_DB=sqlite`.

## Files
- `backend/app/incidents/detector.py`, `backend/app/incidents/priority.py`, `backend/app/investigation/rca.py`
- tests: `backend/tests/unit/test_detector.py`, `test_priority.py`, `test_rca.py`
- notes: `docs/notes/campaignpilot.md`, `docs/notes/third-party-a3.md`

## Public interfaces
### detector
- `async detect_incidents(session, org_id, *, config=None, now=None) -> list[Incident]`: reads Signals (last 45 days),
  PromptClusters, Organization.personas; runs the pure core; skips drafts that match an OPEN incident on
  (category family, prompt_cluster_id) (open = state not in closed/dismissed/failed/rewarded); inserts new
  `Incident` rows (state `detected`, `investigation_status="not_started"`) and flushes. It does NOT commit, emit
  events or write audit rows: the caller (A12 `services/incidents.py`) should commit, `audit(...)` and emit.
  `Incident.number` is filled by A12's before_insert listener.
- Pure core: `detect_from_series(points: Iterable[SignalPoint], *, context=DetectionContext, config=DetectionConfig, now) -> list[IncidentDraft]`.
  `SignalPoint(metric, value, observed_at, kind, baseline, prompt_cluster_id, source, raw, signal_id)`. Drafts carry
  title, category, metrics (`MetricDelta` label/before/after/delta/unit + key, delta_pct), first_observed_at,
  confidence, summary, priority (`PriorityResult`), severity, dedup_key, context. Also `filter_duplicates`, `RULES`.
- Incident row fields written: title, category, severity, priority (0-100), priority_breakdown (`PriorityResult.to_dict()`),
  state, detected_at, first_observed_at, prompt_cluster_id, metrics (list of MetricDelta dicts, max 4), confidence,
  summary, context (`dedup_key`, `family`, `below_action_threshold`, `unknown_priority_components`, `signal_ids`,
  `detection[]` with baseline_source/z/magnitude, `topic`).

### Signal contract the detector reads (A7 ingestion please normalise to these `Signal.metric` names)
`visibility`, `citation_share`, `competitor_share` (raw `competitor` = subject), `prompt_volume`, `accuracy`,
`factual_conflicts`, `cited_sources`, `lost_sources`, `new_competitor_content`, `stale_sources`, `avg_position`.
Aliases in `METRIC_ALIASES`; if `metric` is unknown `Signal.kind` is tried. Ratio metrics may be 0..1 or 0..100
(auto-detected per series; shown as `pp`). One row per observation per day per (metric, cluster, subject, source);
`prompt_cluster_id` should be set where possible (None = org-wide). Optional raw hints: `buyer_intent` (0..1),
`persona`. `Signal.baseline` is only used when fewer than 3 history points exist.

### priority
- `compute_priority(*, prompt_demand, buyer_intent, incident_severity, persona_importance, competitive_displacement, evidence_confidence, remediation_feasibility, config=None, sources=None, notes=None) -> PriorityResult`.
  Each arg 0..1 or `None` (= not measured: neutral config default, flagged `source="default"`).
  Score = 100 x weighted geometric mean of the 7 components (equal weights default; multiplicative semantics, zero kills,
  all-0.7 -> 70). `PriorityResult.score` 0..100; `.breakdown[name] = ComponentScore(value 0..1, display 0..100, weight, source, note)`;
  `.to_dict()` JSON for `Incident.priority_breakdown` (`{"score","method","components":{name:{label,value,display,weight,source,note}}}`).
- `severity_from_priority(score, thresholds=SeverityThresholds(critical=70, high=50, medium=30)) -> Severity` (else low).
- `PriorityConfig` (weights, defaults, feasibility per category, demand half-saturation 500, displacement full-scale 30pp,
  `observe_below=15`), `is_below_action_threshold`, helpers `demand_from_volume`, `displacement_from_competitor_gain`,
  `buyer_intent_from_prompts` (keyword heuristic), `persona_importance_from_config`, `feasibility_for_category`.
- Detection-time `evidence_confidence` = detection-signal confidence only. After investigation, recompute with
  hypothesis/evidence confidence (call `compute_priority` again) and update `Incident.priority`/`severity`.
- A9: the priority components are a natural part of the bandit context vector (use `breakdown[...].value`).

### rca
- `generate_hypotheses(incident, evidence, llm=None) -> list[HypothesisDraft]`; richer: `run_rca(...) -> RCAResult(hypotheses, warnings, llm_used)`;
  async: `agenerate_hypotheses` / `arun_rca` (LLM `complete_json` may be a coroutine).
- `HypothesisDraft(rule_id, layer, title, summary, confidence 0..0.9, evidence_ids, contradicting_evidence_ids, rationale, missing_evidence, produced_by, status=proposed, actionable)`.
  Map to `Hypothesis` rows: title, summary, status, confidence, evidence_ids, rationale, produced_by (layer/rule_id/missing_evidence are extra: store in
  `Hypothesis` description or a JSON column if A6 adds one; `layer` is useful for UI grouping).
  `status` can only be `proposed` (validator). The fallback `no_actionable_cause` (layer `undetermined`, `actionable=False`) is
  added when the best rule confidence < 0.5; the evidence gate must never confirm it.
- `Layer` = ai_engine, citation, owned_content, competitor, external_web, canonical_truth (+ undetermined).
- `LLMClient` Protocol: `complete_json(*, system, prompt, schema) -> str | dict`. Output must validate against `LLMOutput`
  (`hypotheses[{layer,title,summary,confidence,evidence_ids(non-empty),rationale}]`); unknown evidence ids discard that item;
  invalid payload / exception / async client in sync entrypoint -> discarded, rules only. LLM confidence capped at 0.8; duplicates of a rule
  hypothesis (same layer + same evidence set) skipped. Evidence text is passed inside `<evidence>` tags with an
  "untrusted data" system instruction (prompt-injection guard).
- Evidence is duck-typed (ORM `Evidence`, dict, or object). Hints in `raw` that rules use (A4 collector / A7 ingestion should set them where known):
  profound `citation_change` in {added,lost,replaced}, `prompt_change`, `model_update`; competitor `changed`/`is_new` (or status `changed`);
  owned `buried`, `click_depth`; external `cited_by_ai`. Other inputs: `type`, `status`, `support_score`, `contradiction_score`, `freshness_risk`, `confidence`, `url`.

## Known gaps / assumptions
- Thresholds, feasibility priors, intent keywords, confidence formulas are hand-set heuristics (not learned, not calibrated).
- Detector dedup is by (category family, cluster) against open incidents only; no cooldown after close, so a persisting anomaly re-opens after closure.
- Persona importance needs `Organization.personas` entries shaped like `{"name", "importance"|"weight"}` and a `persona` hint in signal raw; otherwise flagged default.
- Competitor displacement is only measured when `competitor_share` signals exist for the cluster; otherwise flagged default (0.3).
- Hypothesis `layer`/`rule_id`/`missing_evidence` have no column on `Hypothesis`; persisting them needs A6/A12 (suggestion: put in `produced_by` as `rules:<rule_id>` which is already done, and layer in `rationale`/JSON).
- No LLM client implementation is provided; whoever owns the model connector should implement `LLMClient`.
- No requests filed in `docs/notes/requests-a3.md`; nothing needed from non-owned files.
