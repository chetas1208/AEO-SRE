# Handoff G3 (Change Guard frontend)

STATUS: built against the spec field names; **API STABLE not yet seen** (no docs/notes/handoff-g1.md when this was written), so
`pnpm gen:api` was NOT run and `types/api.generated.ts` is untouched. Nothing staged/committed; backend untouched; fixture EXP-0001 never touched.

## Remaining after API STABLE
1. `cd frontend && pnpm gen:api`, then reconcile `utils/normalize.ts` (section "Change Guard": normChangeCheck / normFinding / normProtection /
   normCanonicalClaim; `normIntervention` reads `changeGuard`). Names I guessed and must be checked: `change_guard` on interventions,
   findings fields (`target_overlap_pct`, `prompt_cluster_overlap_pct`, `references.{experiment_code,other_change_id,canonical_claim_id}`,
   `outcome_class`), `checks_run`/`checks_skipped`, `semantic_check` values (`ran|degraded|skipped_no_canonical_truth`; `semanticRan` in
   `utils/guard.ts` treats ran/ok/full/ready as "ran"), `merged_proposal`, overlap unit (rendered as given, assumed 0-100), list envelope
   (`items,total,limit,offset`), canonical-claims list (`?include_retired=true` is a guess), SSE names (`change_check.created/decided` as named
   events; also bumps on `incident_event` with `event_type` starting `change_check.`), `review_reason` approve body key (per brief).
2. Run `bash scripts/browser_smoke.sh`; the real-data tests in `e2e/guard.spec.ts` un-skip once the API serves protection/checks.
3. Scratch run (not done: needs G1 helpers): `PLAYWRIGHT_SCRATCH=1 CHANGE_GUARD_TOKEN=... GUARD_TEST_ORG_DOMAIN=... PLAYWRIGHT_BASE_URL=http://127.0.0.1:3050
   PLAYWRIGHT_API_BASE_URL=http://127.0.0.1:8050` on DB `aeo_g3`, then tear down.

## What was built
- Types (`types/index.ts`): GuardDecision, GuardFinding, ChangeCheck, ChangeCheckList, ExperimentProtection, CanonicalClaim; `InterventionCandidate.guard`, `ExperimentDetail.protection`.
- `utils/guard.ts`: `decisionMeta` (label+glyph+tone, UI.md 43; never colour only), `approveGate` (BLOCK/DELAY disable; REQUIRE_REVIEW needs non-blank reason;
  null = unavailable, not blocked client-side, server still enforces), `semanticCheckLabel`, `guardErrorMessage` (CHANGE_GUARD_BLOCKED, APPROVAL_DIGEST_MISMATCH
  -> "The proposal changed since approval; re-approve."). `useApi.toApiError` uses it via `error.code`. No overlap/eligibility/contradiction logic client side.
- Composables: `useChangeChecks` (paged, org/experiment/decision filters, refetch on SSE tick), `useCanonicalClaims` (X-Actor header, soft retire, refetch after writes),
  `useApproveIntervention.approve(id, note, reviewReason)` -> `review_reason`; refetches on the two guard error codes. `apiFetch` gained a `headers` option.
  `stores/liveSystem.ts`: `changeCheckTick`.
- Components (`components/guard/`): DecisionChip, CheckCard (SIMULATED AGENT badge, agent, target, findings+reasons, eligible_after, overlap %), ProtectionBanner
  ("Protected until YYYY-MM-DD HH:MM UTC" + relative), ChangeChecksPanel (This experiment / All recent, decision filter, paging, live), GuardVerdict.
  `components/settings/CanonicalClaimsEditor.vue` (Settings -> Organization; empty state explains the semantic check is skipped until claims exist).
- Wiring: `pages/experiments/[id].vue` (banner + panel; "Protection status unavailable" when `protection` is absent), `pages/experiments/index.vue` (feed in the empty state),
  `IncidentActionPanel.vue` (verdict, Approve disabled on BLOCK/DELAY), `ApprovalDialog.vue` (verdict, review reason, gating). These files had no uncommitted edits by others (git status clean), so requests-g3.md was not needed.
- Note: `/experiments` redirects to the first experiment, so the feed lives on the experiment detail page ("All recent").

## Tests / verification
- `pnpm test` 68/68 (new `tests/guard.test.ts`: normalizers, chip mapping, gating, unavailable handling, component rendering, error codes), `pnpm typecheck` OK, `pnpm build` OK.
- `bash scripts/browser_smoke.sh` on the dev stack: 15 passed, 4 skipped (real-data guard tests skip until the API serves protection/checks; scratch-only tests skipped).
- `e2e/guard.spec.ts`: read-only real-click tests + a MOCKED-response test (browser-injected fixtures, backend untouched) that produced
  `frontend/test-artifacts/guard/protected-experiment-banner-mock.png` and `change-checks-block-delay-mock.png`; `canonical-truth.png` is from the live dev stack.
  Real-data screenshots (`protected-experiment-banner.png`, `change-checks-block-delay.png`) appear when the API ships.
- Chromium needed `LD_LIBRARY_PATH=~/.local/beatit-libs/root/usr/lib/x86_64-linux-gnu`; no libasound problem hit.
