# W1 requests for off-limits files (Gemini-owned or 3D)

1. `frontend/assets/css/main.css` / `tailwind.config.ts` / `nuxt.config.ts` (dirty in the working tree): with the current tree
   the app shell renders broken. On both the main Nuxt (:3000) and a scratch Nuxt (:3010) the left nav rail spans the whole
   viewport (screenshot: scratchpad `w1_peek.png`), and it intercepts pointer events over page content (Playwright
   `click()` on the incident "Action" tab, Approve and "Mark as executed" times out with "`aside.rail` intercepts pointer
   events"). Suspect: `@nuxtjs/tailwindcss` added in nuxt.config.ts (preflight reset / layout classes) or the `.rail`/layout
   grid rules in main.css. The W1 end-to-end run therefore used `locator.evaluate(el => el.click())`. The committed
   `frontend/e2e/smoke.spec.ts` may hit the same problem for any click on content under the rail.
2. `frontend/nuxt.config.ts` sets `components: [{ path: '~/components', pathPrefix: false }]`, so component names are the bare
   file names. `layouts/default.vue` uses `<VisualizationAmbientField />` and
   `components/incidents/IncidentEvidenceGraph.vue` uses `<VisualizationEvidenceScene />`; both are unresolved at runtime
   (Vue warn "Failed to resolve component"). They are 3D components, left untouched. Fix: use `<AmbientField />` and
   `<EvidenceScene />` (or drop `pathPrefix: false`). W1 fixed the same problem in `pages/experiments/[id].vue`
   (it used `<ExperimentsExperimentSpine>` etc., which never rendered at HEAD).
3. `frontend/components/incidents/ApprovalDialog.vue` / `IncidentActionPanel.vue`: `useApproveIntervention` now returns `null`
   (no throw) and sets `message` ("Already recorded...") when the server answers 409 with `details.reason` `already_*`
   (repeat of a mutation). Callers that print "submitted" after `await api.approve/reject/modify/execute` should read
   `api.message.value` and show it instead of a success claim when the return value is `null`. Approve/reject repeats return
   200 (retry-safe), so only `/executed` and `/execute` paths can return 409-already.
