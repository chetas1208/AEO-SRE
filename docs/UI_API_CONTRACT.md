# UI <-> API contract

Source of truth for what each screen reads from the backend. Enforced by `backend/tests/api/test_ui_contract.py`
(OpenAPI paths, methods, response fields, error envelope, SSE content type) and `frontend/tests/ui-contract.test.ts`
(generated types, normalizers, error mapping, global SSE). Regenerate types with `cd frontend && pnpm gen:api`.

Wire format is snake_case; `utils/camelize.ts` camelizes it and `utils/normalize.ts` maps it to view types. The client
never computes an outcome, reward, eligibility or state: it renders what the server says (UI.md 55).

## Global rules

- **Loading**: `LoadingState` while the first request is in flight.
- **Unavailable vs failed**: no HTTP status, 502/503/504, or `error.type == "unavailable"` -> `kind: unavailable`
  ("X unavailable"); any other non-2xx -> `kind: failed`. Both render `ErrorState` with Retry.
- **Empty**: a successful response with zero items -> `EmptyState` (never an error).
- **Unavailable field**: a null/absent field renders "Unavailable" (or a "not assessed yet" sentence). No default, placeholder
  number, invented score, or hardcoded org/policy label is ever shown.
- **Error envelope**: `{"error": {"code", "type", "message", "details", "request_id"}}`. `useApi.toApiError` reads `error.code`
  first, falls back to `error.type`, and keeps `details` and `request_id` on `ApiErrorInfo`.
- **Mutations are never optimistic.** After a POST the composable refetches and renders server state.
  A 409 whose `details.reason` starts with `already` (repeat of `/executed`) or code `DUPLICATE_REWARD` is treated as
  already applied: refetch, show "Already recorded", no error. Other 409s stay errors.

## Error codes the UI handles

| code | status | UI behaviour |
|---|---|---|
| `EXPERIMENT_NOT_VERIFIABLE_YET` | 409 | "Not eligible yet: the verification window opens <time> (eligible at <details.eligible_at>)" on the experiment page |
| `ACTION_NOT_ELIGIBLE` | 409 | approval refused: error shown, no refetch-as-success |
| `INVALID_STATE_TRANSITION` | 409 | shown as an error (e.g. approve after reject; recorded decisions are final) |
| `DUPLICATE_REWARD` | 409 | treated as already applied |
| `DATABASE_UNAVAILABLE`, `PROVIDER_UNAVAILABLE`, `PROVIDER_NOT_CONFIGURED`, `QUEUE_UNAVAILABLE` | 503 | `unavailable` |
| `PAYLOAD_TOO_LARGE` | 413 | failed |
| `NOT_FOUND` | 404 | failed (detail pages show ErrorState) |
| `validation_error` (type; code `VALIDATION_ERROR`) | 422 | failed, message shown |

Anything else is shown as "API request failed (HTTP n)" plus the server message.

## SSE

| route | events | used by |
|---|---|---|
| `GET /api/events` (text/event-stream) | named `heartbeat` (every 10s), `incident_event` (every persisted incident event; has `id`, `stage`, `event_type`). No replay. | `stores/liveSystem.ts`, started by `AppTopBar` |
| `GET /api/incidents/{id}/events` (text/event-stream) | default `message` events, SSE `id` = seq, reconnect replays persisted events after it; payload has `stage`, `event_type`, `status`, `message`, `metadata` | `useIncidentEvents` |

Live pill (`AppTopBar`, `data-testid="live-pill"`, `data-state`): `Live Ns` (connected, N = seconds since last heartbeat),
`Reconnecting` (error or no heartbeat for 25s; exponential backoff 1s..30s), `Offline` (API unreachable or stream
disconnected), `Degraded` (stream fine but a capability is not healthy). Never green unless the API answered and heartbeats arrive.
`incident_event`s are deduped by `id`; each new one (and each reconnect, since there is no replay) bumps
`liveSystem.incidentEventTick` so lists can refetch. The stream is closed on unmount.
`event_type` vocabulary (`app/core/event_types.py`): investigation.started, provider.request.started/completed,
hypothesis.proposed/confirmed, policy.completed, approval.requested, experiment.activated, verification.scheduled/completed,
reward.created; unknown stages keep their own name. The incident page refetches on state-changing types and on any
success/failed/warning step.

## Screens

### Shell (all pages)
- `GET /api/health`: `status, database, redis, profound_state, model_state, git_sha`.
- `GET /api/system/capabilities`: `capabilities[] {key,label,state,detail,last_success,last_error}`, `executors`, `last_ingestion`.
- `GET /api/organizations`, `POST /api/organizations`, `GET/PATCH /api/organizations/{id}`.
- `GET /api/events` (see SSE).
- Empty: no organizations -> "No organization". Unavailable: API unreachable -> pill Offline.

### Incidents list (`/incidents`)
- `GET /api/incidents` (`limit` 1..200, filters): `items[] {id, number, title, severity, state, status, display_state, detected_at, org_id, primary_delta, trend, investigation_status}`, `total`, `counts_by_*`.
- The row label is `display_state`; the UI does not derive it from `state`.

### Incident detail (`/incidents/{id}`)
- `GET /api/incidents/{id}`: `metrics, priority_breakdown, primary_cta, allowed_actions, allowed_next_states, expected_outcome, experiment_id, explanation, active_job`.
- Tabs: `/evidence` (`limit` 1..1000, `offset`), `/evidence/{evidence_id}`, `/graph`, `/hypotheses`, `/prompts`, `/interventions`, `/events` (SSE), `/events/history`.
- Mutations: `POST /investigate`, `POST /resolve`, `POST /api/interventions/{id}/approve|reject|modify|execute|executed`.
- **Server-provided state -> CTA**: `primary_cta {key, label, enabled, reason, intervention_id, target_tab}`. Keys: `investigate` (start or retry), `review` (-> evidence tab), `approve` (-> action tab, `intervention_id`), `mark_executed` (manual package issued, human must apply it), `execute` (explicit activation), `resolve`, `none` (disabled, `reason` shown). A disabled CTA always shows its `reason`.
- Intervention card states come from `approval_status`, `manual_execution_pending` (shows the package + "Mark as executed" form), `package`, `execution`.
- Unavailable: `expected_outcome.insufficient` / `unavailable_reason` -> "insufficient history", never a range.

### Experiments list (`/experiments`)
- `GET /api/experiments` (`status` raw or `running|awaiting_measurement|verified`, `incident_id`, `org_id`, `action`, `limit`, `offset`).
- Fields: `items[] {id, code, status, display_status, outcome, inconclusive_reason, reward, before, after, policy_version, dry_run, executor, awaiting_human_execution, deviation, measured}`, `summary {running, awaiting_measurement, verified, total}`, `unavailable_reason`.
- Label shown is `display_status`: Proposed, Awaiting Execution, Running, Awaiting Measurement, Verified, Inconclusive, Rejected, Failed, Never Measured (dry run). Inconclusive experiments keep `status = verified`; `display_status = Inconclusive` and `outcome = inconclusive` carry the distinction.

### Experiment detail (`/experiments/{id}`, UUID or `EXP-0001`)
`GET /api/experiments/{id}` sections (UI.md 30-32):
- `summary` (number, status, display_status, incident, action, timestamps, window, dry_run), `why_selected` (reason, selection_basis, cold_start, policy_probability, policy_version, alternatives, rationale, risk), `context_at_decision`, `evidence_snapshot`, `action_executed`, `approval`, `before_metrics`, `after_metrics`, `reward`, `policy`, `timeline`, `awaiting_reward`.
- `display_status` (same vocabulary as the list).
- `spec` -> `ExperimentHypothesis`: `if_action`, `because_root_cause`, `then_metric`, `direction`, `window_hours`, `delay_hours`, `statement`, `declared_at`. Null -> "Unavailable: no hypothesis was recorded".
- `declared_metrics {primary, secondary[]}`. Null -> "Unavailable".
- `outcome` -> `ExperimentVerdict`, `PotentialConfounders`, `RewardBreakdown`: `label` (favorable|unfavorable|neutral|inconclusive), `observe_outcome`, `reward_total` (null when no learning), `components`, `confounders[] {kind, detail, hard}`, `causal_confidence`, `causal_statement`, `learning_applied`, `inconclusive_reason`, `observed_at`. Null -> "Not assessed yet"; confounders show "not assessed yet", never "none detected".
- Inconclusive: label "Inconclusive" + "No conclusion could be drawn: <inconclusive_reason>. No policy update was made".
- `override {overridden, policy_action, executed_action, reason, by}`: shown only when `overridden`.
- `verification {executed_at, eligible_at, window_end, delay_hours, is_open, rules[]}`: eligibility is computed by the server clock (`is_open`); the client never compares timestamps for gating.
- `POST /api/experiments/{id}/verify` ("Request verification", shown for `executed`/`awaiting_verification`, not dry runs): 202 + job; 409 `EXPERIMENT_NOT_VERIFIABLE_YET` before the window opens (`details.eligible_at`), repeats return the in-flight job.
- Unknown values render "Unavailable". Policy version, rewarded-experiment count, selection probability and per-action scores are shown only when the server sent them.

### Settings (`/settings`)
- `GET/PATCH /api/settings`, `GET/PATCH /api/policy`, `GET /api/policy/versions`, `GET /api/system/capabilities`.
- `PolicyOut.available == false` -> unavailable with `unavailable_reason`. `observe` can never be disabled (server enforces).

## Not wired / known gaps
- No UI for rejection feedback analytics (`app/learning/feedback.py` is not exposed over HTTP).
- No list endpoint for historical policy decisions; per-action scores come only from `why_selected.alternatives`.
- `GET /api/events` has no replay; pages refetch via `incidentEventTick` instead.
