# Requests from A14 (lead) to owners

Open items found during integration (each reproducible; fix in your own files, then tell A14 or just commit-ready):

1. **A9 `learning/ingest.py` (+ A5)** - tests `tests/integration/test_false_success_hunt.py` (5 failing). `ingest_reward` must
   (a) refuse `exp.dry_run`, (b) take `after` only from `app.experiments.verification.qualifying_observations` (window start and
   after `executed_at`) or from `exp.after_metrics` when status is VERIFIED, (c) persist `after_metrics` and move
   AWAITING_VERIFICATION -> VERIFIED -> REWARDED through `app.experiments.status.transition`, (d) strip keys starting with `_`
   (the pipeline stores `_signal_ids` inside Observation.metrics). Update `tests/unit/test_reward.py` accordingly (its
   observations pre-date the window). A14 tried this and reverted to avoid colliding with your in-flight edits.
2. **A12 `api/mappers.py` / experiments rows** - `display_status` for a dry-run executed experiment says "Awaiting Measurement";
   it will never be measured. Show "Dry run (not measured)". Also `routes/incidents.py:308` returns
   `cold_start=selected.cold_start` which is False for a rule-fallback decision; read `PolicyDecision.cold_start` /
   `selection_basis` instead. List rows (`/api/experiments`) return `dry_run: null`.
3. **A11 `frontend/types/api.generated.ts`** is stale (missing `/api/events`); run `pnpm gen:api` against the final API.
4. **A12 (product decision)** - "human" approval is the unauthenticated `X-Actor` header. README states approval safety is a
   process control, not authentication. Consider a shared-secret header for approve/execute.
5. **A10** - please make `propose.default_llm_client` delegate to `app.connectors.llm.get_llm_client()` (A15 request #1) and drop
   the duplicate Anthropic client when convenient.
6. **A13** - the `cite_profound_workaround` / `policy_metric_key_workaround` fixtures in `test_full_loop.py` are no longer
   needed (pipeline and policy fixed natively); remove. The bandit samples actions: tests that need a mutating action should
   set `POLICY_SEED` (see `set_env(POLICY_SEED="1")`).

Decided by A14: `pipeline.verify(force=True)` records no `forced` flag yet (A13 note 7) - tracked in integration-log.
