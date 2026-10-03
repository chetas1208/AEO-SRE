# Handoff U1 (UI defect fixer)

No design changes (no CSS/colour/typography/layout edits). Fixture EXP-0001 was never touched; the main stack was only read (GET) and
the approval dialog was opened then cancelled. Nothing staged or committed. `screenshots.spec.ts` untouched. Backend untouched.

## Defects, root cause, minimal fix
1. Nav rail spanning the viewport / intercepting clicks. Root cause was NOT the CSS: `<VisualizationAmbientField>` was an
   unresolved component, so Vue rendered an unknown `<visualizationambientfield>` element as a grid child of `.shell`. It took
   column 1 (218px) and pushed `aside.rail` into the 1fr column (rail box measured x=218, w=1222). Fixing (2) restores the rail to
   x=0, w=218. No CSS changed.
2. Tags renamed to the bare names (`pathPrefix: false`): `layouts/default.vue` -> `<AmbientField />`,
   `components/incidents/IncidentEvidenceGraph.vue` -> `<EvidenceScene />`. Now render (2 canvases, console clean).
   Graceful degrade without removing 3D: new `frontend/utils/webgl.ts` `canRender3d()` (false for `navigator.webdriver`,
   prefers-reduced-motion, or no WebGL context). `AmbientField.vue` and `EvidenceScene.vue` early-return when false;
   `IncidentEvidenceGraph` defaults `viewMode` to `'2d'` (existing SVG graph) in that case; the 3D/2D toggle still works.
   Verified in a non-webdriver Chromium (SwiftShader): graph visible in 1.6s, 2 canvases, no console errors.
3. `ApprovalDialog.vue`: if `api.approve` returns `null`, shows `api.message` (new `alreadyMsg`, tone-warn status line).
   `IncidentActionPanel.vue`: modify/reject set `done` to `api.message` when the call returns null. (`InterventionPackage`
   already handled `/executed`.)
4. Slow smoke. Causes: (a) the unresolved-component grid break and (b) the Nuxt dev server's file watcher picked up Playwright's
   writes under `frontend/test-artifacts` (traces/reports/screenshots) and full-reloaded pages mid-test (seen as endless
   "page reload test-artifacts/..." lines), plus software-GL 3D init in headless. Fix: `nuxt.config.ts`
   `vite.server.watch.ignored: ['**/test-artifacts/**', '**/e2e/**']`, and the webdriver 3D skip above. Result: smoke 4/4 in 7.9s
   (each test 1.3-2.0s); whole suite 13 passed in 18s. NOTE: the already-running main Nuxt (:3000) predates the nuxt.config change;
   restart it to pick up the watcher ignore.
5. Scratch verification (see below) found two wiring defects, fixed:
   - `ExperimentSummary.vue`: "Policy Version" card showed `model` (it stripped "Policy" from the capability label "Policy model").
     Now uses the first real `policyVersion` from list rows, else `unavailable` (removed the capability lookup). Shows `v0.0.1`.
   - `ExperimentHypothesis.vue`: THEN row rendered "Visibilityshould increase" (Vue trimmed the leading space in the span).
     Now `{{ ' ' + direction }}`.

## New test: `frontend/e2e/clicks.spec.ts` (real pointer clicks only, no force / el.click())
- rail occupies only its column and does not cover an incident card (`elementFromPoint`)
- click incident card -> Action tab -> Approve (opens dialog) -> Cancel
- click experiment queue card (note: `/experiments` redirects to the first experiment; `ExperimentTable.vue` is not mounted anywhere,
  the list UI is `ExperimentQueue`/`ExperimentCard`)
- approve -> Close -> "Mark as executed" -> form gone. Runs only with `PLAYWRIGHT_SCRATCH=1` (mutating; skipped on the dev fixture).

## Scratch stack evidence (torn down: DB `aeo_u1` dropped, redis db 9 flushed, :8030/:3030 stopped)
Seeded with clearly-TEST data through the test factories (`tests/factories.py`, same ORM path as the API tests; script was in the
session scratchpad): verified+rewarded experiment (Reward row, favorable outcome, policy version), inconclusive experiment
(overlapping_intervention), manual-override experiment awaiting measurement, and an incident awaiting approval. Notes for whoever
repeats it: API needs `APP_BASE_URL=http://127.0.0.1:<nuxt port>` (CORS only allows that origin and :3000); approving needs a
PolicyDecision row, incident `metrics` with an `after`, a real `ProposedChange` (`kind=page_change`, files), and no other active
experiment in the same org/cluster.
Observed in the browser with real API fields: `/experiments` summary + queue; rewarded (Favorable, reward +0.21 with
decomposition and weights, spine steps 8/9 complete, approval row); inconclusive (reason, low confidence, confounder, "No reward");
manual override (policy chose Observe / executed Update existing page / reason / by alice, Request verification, eligible window);
incident detail with evidence graph (2D in headless). Remaining "Unavailable" strings were all genuinely absent in the seeded data
(`observed_at`, per-action scores, policy version on rows without one). Full-page PNGs were in the scratchpad (`u1_*.png`).

## Verification
`pnpm typecheck` OK, `pnpm test` 48/48, `pnpm build` OK, `bash scripts/browser_smoke.sh` on :3000: 13 passed, 1 skipped (scratch-only).
`pnpm gen:api` not needed (API unchanged).

## Remaining / observations (not changed)
- Metric strip renders unit text twice for `unit: ratio` ("0.61 ratio -> 0.37 ratio"); likely unit-specific formatting, only seen with
  my invented unit. Inconclusive reason shows the raw token (`overlapping_intervention`).
- When approve succeeds but activation fails, the backend answers approved with a `message`; the dialog still says "Approval
  submitted" (the incident then shows no package). Worth surfacing the server message if the response carries it.
- `ExperimentTable.vue` is dead code.
