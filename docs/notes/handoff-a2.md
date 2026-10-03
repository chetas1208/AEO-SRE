# Handoff - A2 (BlackBox reverse-engineering)

## Delivered
- `references/blackbox-datahub/` @ `b72a32c64e11404b1de98b517bde7e487ca35963`, Apache-2.0 (no code copied; see `docs/notes/third-party-a2.md`, lead merges into `THIRD_PARTY.md`).
- `docs/notes/blackbox.md`: findings + machine-readable reuse table (between `REUSE_TABLE_START/END`).
- `backend/app/incidents/state_machine.py`, `backend/app/investigation/evidence_gate.py`.
- Tests: `backend/tests/unit/test_state_machine.py` (63), `test_evidence_gate.py` (54); run `cd backend && .venv/bin/python -m pytest tests/unit/test_state_machine.py tests/unit/test_evidence_gate.py` -> 117 passed; ruff clean.

## Public interfaces

### `app.incidents.state_machine`
- `ALLOWED: dict[IncidentState, set[IncidentState]]`, `TERMINAL_STATES` (closed, dismissed, failed), `IllegalTransition(ValueError)` (`.src`, `.dst`).
- `can_transition(src, dst) -> bool` (accepts enum or str), `next_states(src)`, `is_terminal(state)`, `shortest_path(src, dst)` (unguarded route, for pipelines that need to walk).
- `transition(incident, dst, actor, reason, *, metadata=None, gate=None, on_audit=None, now=None) -> dict`: mutates `incident.state` only after all checks; returns audit record `{incident_id, entity_type:"incident", event:"state_transition", from_state, to_state, actor, reason, metadata, at}`. **Caller persists it** (e.g. `app.core.audit.audit(...)` + `EventBus.emit`); the machine does no I/O. `actor` and `reason` must be non-empty.
- Graph: linear happy path detected -> triaged -> investigating -> evidence_ready -> root_cause_proposed -> root_cause_confirmed -> intervention_proposed -> awaiting_approval -> approved -> executing -> executed -> awaiting_verification -> verified -> (rewarded ->) closed. Loops: evidence_ready/root_cause_proposed -> investigating; awaiting_approval -> intervention_proposed (reject/modify). dismissed/failed from every non-terminal state. Verified -> closed allowed (e.g. observe actions with no reward). No self-transitions, no edges out of terminals, no path skips awaiting_verification/verified.
- Guarded edge: `root_cause_proposed -> intervention_proposed` is legal only with `metadata={"action": "observe"}` (low-confidence root cause -> observe). Pass `gate=<GateResult>` when moving to `root_cause_confirmed` to bind it to the evidence gate (unconfirmed gate raises).

### `app.investigation.evidence_gate`
- `confirm_aeo_root_cause(hypothesis, evidence, policy=None, *, incident_at=None, category=None) -> GateResult`. Pure, duck-typed (objects or dicts), never mutates, caller flips `Hypothesis.status`.
- `GateResult(confirmed, reasons, missing, confidence, checks, supporting_ids, contradicting_ids, contradictions, ignored)` + `.to_dict()`. `reasons` = failure explanations when not confirmed (one per `missing` code), pass descriptions when confirmed. `missing` codes: `profound_signal, metric_change, content_evidence, citation_evidence, timestamp_alignment, rationale, aggregate_confidence, hypothesis_confidence, cited_evidence, contradictions, hypothesis_state` (constants exported in the module).
- `EvidencePolicy` (frozen dataclass, defaults in `DEFAULT_POLICY`): require_* flags, `min_aggregate_confidence=0.6`, `support_threshold=0.5`, `contradiction_block_threshold=0.7`, alignment window (24h after / 30d before incident), content types (owned/competitor/external), citation categories (`competitor_citation_gain`, `lost_citation_source`), etc.
- Semantics: unavailable/failed never count (cannot be configured away); `inference` evidence never supports; support strength = `support_score`, else `confidence`, else neutral (never invented); content evidence must have excerpt or content_hash; metric-change = non-zero `metric_delta` attr or `delta/change/pct_change/delta_pct/z_score` or differing `before/after|baseline/value|previous/current` anywhere (depth <= 3) in `raw`; citation evidence = has `url` and (`is_citation` or raw `kind` in citation kinds); contradictions are collected from **all** usable evidence (cited or not), reported in `contradictions`, penalise aggregate, and block at score >= 0.7.

## Integration requirements (important for other agents)
1. **A3 (rca)**: hypotheses must fill `evidence_ids` (gate rejects hypotheses citing nothing/unknown ids; `EvidencePolicy(require_cited_evidence=False)` relaxes it) and a written `rationale` (>= 20 chars). Provide incident time via `hypothesis.incident_at` / `hypothesis.incident.first_observed_at|detected_at` or pass `incident_at=`/`category=` kwargs; the pipeline (A14) should pass `incident_at=incident.first_observed_at or incident.detected_at` and `category=incident.category`. Without an incident time the earliest supporting Profound `observed_at` anchors alignment; with neither, alignment fails.
2. **A4/A6/A8 (collector/evidence/ranker)**: evidence rows need `support_score`/`contradiction_score` (or `confidence`), `observed_at` for content (when the page/claim changed or was published, not crawl time), `excerpt` or `content_hash`, `url`. Profound evidence should carry the measured change in `raw` (e.g. `{"before":..,"after":..}` or `delta_pct`). Citation evidence should set `raw["kind"]="citation"` (or `citation_change`) with a `url`. Unscored evidence is treated as neutral and will never confirm anything.
3. **A14 (pipeline)**: flow = `transition(..., ROOT_CAUSE_PROPOSED)`; run gate; confirmed -> `transition(incident, ROOT_CAUSE_CONFIRMED, "system:evidence_gate", reasons, gate=result, metadata=result.to_dict())`; not confirmed -> stay `root_cause_proposed`, either back to `investigating` or, for low confidence, `INTERVENTION_PROPOSED` with `metadata={"action":"observe"}`. Persist each returned audit record via `app.core.audit` and emit SSE.
4. **A12**: `IllegalTransition` should map to HTTP 409. Incident `state` may arrive as str or `IncidentState`; both work.
5. **A13**: unit tests are self-contained (dataclass stand-ins, no DB).

## Stubbed / known gaps
- Gate does not look at EvidenceEdge (graph) structure; it works off the evidence list. Could add a graph-connectivity requirement later.
- No per-requirement weights; all enabled requirements are mandatory (aggregate confidence is mean support strength minus `contradiction_penalty * strongest contradiction`).
- Aggregate "metric_change" has no minimum magnitude (any non-zero change). Add `min_relative_change` if A3 sees noise.
- State machine does not write `IncidentEvent`/`AuditEvent` itself (by design, no I/O).
- Dismissing/failing from `executing`/`executed` is permitted per brief; callers should record why.

## Requests for others
None required. Suggest A10 read the PR-executor section of `docs/notes/blackbox.md` (branch-prefix allow-list, dry-run default, never-raise result, body from recorded state only).
