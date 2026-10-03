# Handoff W1 (full-stack wiring)

Fixture Experiment 1 (EXP-0001) was only read (GET). All end-to-end UI checks ran on a scratch DB (`aeo_w1`, API :8020,
Nuxt :3010, redis db 7) seeded with TEST factory data; all of it is torn down (DB dropped, redis db flushed, processes stopped).

## What changed
Backend
- `schemas/experiments.py`, `api/routes/experiments.py`, `api/mappers.py`: `ExperimentDetail` now returns `display_status`, `spec`
  (IF/BECAUSE/THEN/AFTER), `declared_metrics`, `outcome` (label, causal confidence, confounders, reward_total, learning_applied,
  inconclusive_reason, causal_statement), `override` (policy vs executed action, reason, by), `verification` (eligible_at,
  window_end, delay_hours, server-clock `is_open`, rules). List rows gain `outcome` and `inconclusive_reason`; an inconclusive
  experiment keeps `status=verified` but `display_status="Inconclusive"`. Only persisted data is read; absent -> null.
- `schemas/common.py`, `api/main.py`: `ErrorEnvelope`/`ErrorBody` documented on every route (400/404/409/413/422/500/501/503);
  `routes/events.py` and `routes/incidents.py` declare `text/event-stream` in OpenAPI.
- Tests: `tests/api/test_experiment_detail_fields.py` (4), `tests/api/test_ui_contract.py` (6).
Frontend
- Types regenerated (`pnpm gen:api`). New: `ExperimentHypothesis.vue`, `ExperimentVerdict.vue` (outcome, inconclusive reason,
  verification window, override, "Request verification"), `composables/useVerifyExperiment.ts`.
- `PotentialConfounders` (null = not assessed), `ExperimentOutcome`/`PolicySnapshot`/`ExperimentSpine`/`ExperimentSummary`/
  `OrganizationSwitcher`: removed invented defaults (28%/6%/42%/1,240 baselines, v0.7, 12 rewarded experiments, 0.42 propensity,
  fake action scores, hardcoded eligibility date, acme.com, sarah@acme.com) -> "Unavailable".
- `pages/experiments/[id].vue`: component tags fixed (`pathPrefix: false` made `<ExperimentsExperimentSpine>` etc. unresolvable,
  the page never rendered at HEAD); now renders hypothesis, verdict, reward breakdown.
- `composables/useApi.ts`: `error.code` first, then `error.type`; keeps `details`/`requestId`; EXPERIMENT_NOT_VERIFIABLE_YET ->
  "eligible at <time>"; `isAlreadyApplied` (409 `details.reason` already_* / DUPLICATE_REWARD). `useApproveIntervention` uses it
  (refetch, `message`, returns null). `InterventionPackage.vue` shows "Already recorded".
- `stores/liveSystem.ts`: global `/api/events` EventSource (heartbeat + incident_event), own backoff reconnect 1s..30s,
  25s heartbeat staleness, dedupe by id, `incidentEventTick`, `heartbeatAgeSeconds`; `AppTopBar` starts/stops it and shows
  `Live Ns | Reconnecting | Offline | Degraded`. `useIncidentEvents` carries `eventType` and refetches on state-changing types.
- Tests: `tests/ui-contract.test.ts` (14), 2 updated/added in `tests/composables.test.ts`.
Docs: `docs/UI_API_CONTRACT.md`, `docs/notes/requests-w1.md`.

## Manual execution flow, verified on the scratch stack through the real UI (headless Chromium)
Incident Action tab -> Approve -> intervention package rendered -> "Mark as executed" -> form gone, incident awaiting measurement
-> experiment page shows IF/BECAUSE/THEN/AFTER, eligible-at, "Awaiting Measurement", "Request verification" -> 409 shown as
"Not eligible yet ... eligible at ..." -> list shows Awaiting Measurement. Live pill reached `degraded` with "Last heartbeat 3s ago"
(degraded only because Profound/model are unconfigured). Caveat: clicks had to use `el.click()` because the nav rail overlays content
in the current dirty CSS (see requests-w1.md #1).

## Still unwired / open
- Dirty Gemini files: shell layout is broken (rail spans the viewport and intercepts clicks); `VisualizationAmbientField` /
  `VisualizationEvidenceScene` tags unresolved; callers in `ApprovalDialog`/`IncidentActionPanel` should surface `api.message`.
- EXP-0001 predates B4 (no `spec`), so its hypothesis card correctly says Unavailable.
- Rewarded/inconclusive rendering was verified by API tests + normalizer tests, not in a browser (no measured fixture exists).
- Rejection feedback analytics and policy-decision history are not exposed over HTTP.
