# A11 Frontend handoff

Nuxt 4 + TypeScript + Vue 3 SPA (`ssr: false`) in `frontend/`, pnpm. Verified: `pnpm typecheck` (vue-tsc, passes), `pnpm build` (passes), `pnpm test` (vitest, 13 tests pass), `pnpm dev` boots, and a Chromium (Playwright) smoke test against the live API at :8000 rendered /incidents, /incidents/:id (all 4 tabs), /experiments, /settings (4 tabs) with no console errors.

## Run
- `cd frontend && pnpm install && pnpm dev` (port 3000). `pnpm build`, `pnpm typecheck`, `pnpm test`.
- `pnpm gen:api` regenerates `types/api.generated.ts` from `${NUXT_PUBLIC_API_BASE_URL}/openapi.json` (reference only; the app uses the hand-written mirror in `types/index.ts` because the API is camelized and several payloads are loosely typed `dict`).
- Env: `nuxt.config.ts` loads the root `../.env` via dotenv; only `NUXT_PUBLIC_API_BASE_URL` reaches `runtimeConfig.public.apiBaseUrl` (fallback `http://localhost:8000`). No frontend-local env file; no secrets in public config. Browser calls the API cross-origin: needs `APP_BASE_URL`/localhost:3000 in API CORS (already present).
- Local test-only addition: `frontend/tests/**` (vitest). Fixtures there are labeled test fixtures.

## Routes
- `/` redirects to `/incidents`.
- `/incidents` (parent `pages/incidents.vue` = queue + `<NuxtPage/>`; `incidents/index.vue` = "No incident selected"), `/incidents/:id` canonical, shareable; `?tab=prompts|evidence|action` for local tabs. Mobile: drill-down (queue then detail with back link). Keys: `j`/`k` next/prev incident, `/` focus search.
- `/experiments`, `/experiments/:id` (accepts UUID or `EXP-0001` via API).
- `/settings` with local tabs Organization / Integrations / Policy / System.
- Layout `layouts/default.vue`: nav rail (Incidents with attention count, Experiments with "N waiting", Settings), SystemStatus, OrganizationSwitcher (list + add domain), top bar (search, Live pill, time range).
- Note: UI.md §60 lists no `pages/incidents.vue`; added as a parent route so the queue does not remount.

## State
Pinia only: `useOrganizationStore` (orgs, current id persisted in cookie `aeo_org`, add via POST), `useLiveSystemStore` (health/capabilities poll every 30s, SSE stream state; Live pill is never green unless API reachable and no stream disconnect; "degraded" if any capability not healthy), `useIncidentSelectionStore` (selected id, severity tab, search query, time range, j/k order).

## Composables (`composables/`)
`useIncidents`, `useIncident` (+ `investigateIncident`, `resolveIncident`), `useIncidentEvents` (EventSource), `useIncidentEvidence` (+ `useIncidentHypotheses`, `useIncidentGraph`, `useEvidenceDetail` lazy excerpt), `useIncidentPrompts`, `useInterventions`, `useApproveIntervention` (approve/reject/modify/execute, no optimistic updates; refetches after), `useExperiments`, `useExperiment`, `useSystemHealth` (+ `usePolicy`, `useSettings`). Base layer `useApi.ts`: `apiFetch` (camelizes keys, throws typed `ApiErrorInfo {kind: unavailable|failed}`), `useApiData` (useAsyncData wrapper). `utils/normalize.ts` maps API payloads to `types/index.ts` and only renames/unwraps (never invents values). Opaque dict keys (`counts_by_status`, `proposed_change`, `allowed_actions`, `context`, `metadata`, ...) are preserved by `utils/camelize.ts`.

## API endpoints and fields relied on
- `GET /api/health`, `GET /api/system/capabilities` (`overall`, `capabilities{key,label,state,detail,last_success,last_error}`, `last_ingestion`, `last_investigation`, `last_policy_update`, `model_artifact_version`).
- `GET/POST /api/organizations`, `PATCH /api/organizations/{id}` (Settings > Organization), `GET /api/settings` (`integrations[]` for Integrations tab).
- `GET /api/incidents?org_id&range&limit` -> `items[]` (`id, number, title, severity, state, status, priority, detected_at, topic, context_label, primary_delta, trend`), `total`, `counts_by_severity`, `counts_by_status`. Severity tabs filter client-side over that single fetch; attention badge = sum of `counts_by_status` for detected/investigating/needs_review/ready_for_action/failed.
- `GET /api/incidents/{id}`: `metrics[]` (max 4 shown), `priority_breakdown{score,components[{key,label,value,source,note}],note}`, `primary_cta{key,label,enabled,reason,intervention_id,target_tab}` (the single primary CTA comes from the backend; local state map is only a fallback), `allowed_actions[]`, `expected_outcome{available,n,low,high,unit,metric,reason}`, `affected_prompt_count`, `first_observed_at`, `confidence`, `summary`, `experiment_id`.
- `POST /api/incidents/{id}/investigate`, `/resolve`. SSE `GET /api/incidents/{id}/events` (default `message` events, JSON `{id, seq, timestamp, stage, status, message, metadata}`; replay then live; heartbeats ignored; failed steps stay visible).
- `GET .../evidence` (items), `.../evidence/{eid}` (excerpt, fetched on row expand), `.../graph` (GraphOut: `nodes{id,node_class,role,title,level,source,url,observed_at,confidence,extract}`, `edges{source,target,type,confidence,conflict,back_edge}`, `validation.warnings`), `.../hypotheses`, `.../prompts` (`items{prompt,intent,volume,our_visibility,competitor,competitor_visibility,engines,change,unit,persona,meta}`, `unavailable_reason`), `.../interventions` (`items{id,action,title,score,risk,reason,selected,selection_basis,policy_version,cold_start,proposed_change{diff,files,target,...},execution_target,rollback,observation_window_hours,approval_status,approval,execution,based_on_experiments}`, `unavailable_reason`).
- `POST /api/interventions/{id}/approve|reject|modify|execute`. Approval dialog: approve, then execute (including for Observe; if execute errors the dialog shows the backend message and the refetched state is authoritative). Choosing an alternative or Observe = approving that candidate's id (selection_basis shown as manual override in the dialog only; the backend should record the override).
- `GET /api/experiments` (`items{code,display_status,before,after,before_after_label,reward,policy_version,...}`, `summary{running,awaiting_measurement,verified}`), `GET /api/experiments/{id}` (sectioned: summary, why_selected, context_at_decision, evidence_snapshot, action_executed, approval, before_metrics, after_metrics, reward, policy, timeline, awaiting_reward).
- `GET /api/policy`, `PATCH /api/policy {allowed_actions:{action:bool}}`.

## Behavior notes
Every surface fails independently (separate composable per surface; ErrorState distinguishes "Unavailable data" vs "Failed data"; EmptyState = no data). If evidence fetch fails, the recommendation is withheld with an explicit error. Expected Outcome renders only an API range with `n`, else "Insufficient experiment history to estimate outcome." Confidence shows number + label. No demo toggles, no hardcoded incidents/metrics (grep-checked); no client-side severity/reward inference.

## Requests to A12 (not blocking)
1. `MetricDelta.favorable: bool|null` (UI.md §13: colour = good/bad for the brand, not sign). Until then deltas render neutral.
2. `GET /api/experiments` has no `org_id` filter, so the org switcher cannot scope Experiments.
3. Experiment before/after: units are undocumented (`before_metrics` is a free dict; values may be 0-1 or 0-100). Please return a MetricDelta-style `outcome[]` (label, before, after, delta, unit, favorable) in `ExperimentDetail`; the table currently shows raw numbers.
4. `InterventionOut`: add explicit `executor`, plus `expected_outcome` per intervention (frontend falls back to `incident.expected_outcome`), and `related_experiments` when a learned policy is used (shown in "learned from N related experiments").
5. Observe: confirm approve + execute on an `observe` intervention is a no-op that leaves the incident open and monitoring (the dialog calls both).
6. `last_ingestion` is null in capabilities today; "Last sync" and the empty-state "Last sync: ..." line show "not reported" until it is populated.
7. Optional: an org-scoped `GET /api/search?q=` (search is currently a client-side filter over the loaded incident queue).
8. Not exercised in browser against live data because no interventions/experiments/evidence rows existed: ApprovalDialog, evidence graph with real nodes, experiment detail. These are covered by typecheck and unit tests only; please run one full investigate -> approve -> execute flow once A5/A10 data exists.
