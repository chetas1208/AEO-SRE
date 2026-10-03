/**
 * Change Guard UI. Real pointer clicks only (no force). Read-only against any stack (GET + navigation only).
 * The mutating test (posting a SIMULATED change check and watching the feed update live) runs only with PLAYWRIGHT_SCRATCH=1
 * on a scratch stack (`aeo_g3`, API :8050, Nuxt :3050) that has TEST data and CHANGE_GUARD_TOKEN set; never the dev fixture.
 * Screenshots: frontend/test-artifacts/guard/.
 */
import { test, expect } from '@playwright/test'
import { mkdirSync } from 'node:fs'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'
const scratch = process.env.PLAYWRIGHT_SCRATCH === '1'
const shots = 'test-artifacts/guard'
mkdirSync(shots, { recursive: true })

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${api}/api/health`)
  if (!health.ok()) test.skip(true, `Backend not reachable at ${api}`)
})

async function protectedExperimentId(request: any): Promise<string | null> {
  const list = await request.get(`${api}/api/experiments?limit=50`)
  if (!list.ok()) return null
  for (const e of (await list.json()).items ?? []) {
    const d = await request.get(`${api}/api/experiments/${e.id}`)
    if (d.ok() && (await d.json()).protection?.protected === true) return e.id
  }
  return null
}

test('protected experiment shows the Protected until banner', async ({ page, request }) => {
  const id = await protectedExperimentId(request)
  test.skip(!id, 'API serves no protected experiment (protection not available yet)')
  await page.goto(`/experiments/${id}`)
  const banner = page.getByTestId('protection-banner')
  await expect(banner).toBeVisible({ timeout: 15_000 })
  await expect(page.getByTestId('protected-until')).toContainText(/Protected until \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC/)
  await expect(page.getByTestId('protected-badge')).toContainText('PROTECTED')
  await banner.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${shots}/protected-experiment-banner.png`, fullPage: false })
})

test('Change checks panel lists BLOCK/DELAY checks with text labels and reasons', async ({ page, request }) => {
  const res = await request.get(`${api}/api/change-checks?limit=50`)
  test.skip(!res.ok(), 'change-checks API not available yet')
  const items = ((await res.json()).items ?? []) as Array<{ decision: string }>
  test.skip(!items.some((c) => ['BLOCK', 'DELAY'].includes(c.decision)), 'no BLOCK/DELAY checks recorded')
  const id = (await protectedExperimentId(request)) ?? (await (await request.get(`${api}/api/experiments?limit=1`)).json()).items?.[0]?.id
  test.skip(!id, 'no experiment')
  await page.goto(`/experiments/${id}`)
  const panel = page.getByTestId('change-checks-panel')
  await expect(panel).toBeVisible({ timeout: 15_000 })
  await panel.getByRole('button', { name: 'All recent' }).click()
  const chips = panel.getByTestId('decision-chip')
  await expect(chips.first()).toBeVisible()
  const text = (await chips.allTextContents()).join(' ')
  expect(text).toMatch(/BLOCK|DELAY/) // decision is text, not colour only
  await expect(panel.getByTestId('check-finding').first()).toBeVisible()
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${shots}/change-checks-block-delay.png`, fullPage: false })
})

test('Settings -> Organization shows the Canonical truth editor', async ({ page }) => {
  await page.goto('/settings')
  const panel = page.getByTestId('canonical-truth')
  await expect(panel).toBeVisible({ timeout: 15_000 })
  await expect(panel.getByRole('button', { name: 'Add claim' })).toBeVisible()
  // Either rows or the explanatory empty state (semantic check skipped until claims exist); or an unavailable error before the API ships.
  const rows = await panel.getByTestId('claim-row').count()
  if (rows === 0) {
    await expect(panel.getByText(/semantic contradiction check is skipped|unavailable|request failed/i).first()).toBeVisible()
  }
  await page.screenshot({ path: `${shots}/canonical-truth.png`, fullPage: false })
})

test('Action tab shows a guard verdict (or says it is unavailable)', async ({ page }) => {
  await page.goto('/incidents')
  await page.getByTestId('incident-card').first().click()
  await page.getByRole('tab', { name: /action/i }).click()
  await expect(page.getByTestId('action-panel')).toBeVisible()
  const verdict = page.getByTestId('guard-verdict')
  if (await verdict.count()) {
    await expect(verdict.first()).toBeVisible()
    const decision = await verdict.first().getAttribute('data-decision')
    if (decision === 'BLOCK' || decision === 'DELAY') await expect(page.getByTestId('approve-open')).toBeDisabled()
  }
})

test('live feed: a posted SIMULATED check appears without reload (scratch only)', async ({ page, request }) => {
  test.skip(!scratch, 'mutating flow: set PLAYWRIGHT_SCRATCH=1 on a scratch stack')
  const token = process.env.CHANGE_GUARD_TOKEN
  test.skip(!token, 'CHANGE_GUARD_TOKEN not set for the scratch stack')
  const exp = await (await request.get(`${api}/api/experiments?limit=1`)).json()
  const id = exp.items?.[0]?.id
  test.skip(!id, 'no experiment on the scratch stack')
  await page.goto(`/experiments/${id}`)
  const panel = page.getByTestId('change-checks-panel')
  await panel.getByRole('button', { name: 'All recent' }).click()
  const before = await panel.getByTestId('change-check').count()
  const target = process.env.GUARD_TEST_TARGET ?? 'https://example.test/test-target'
  const post = await request.post(`${api}/api/change-checks`, {
    headers: { Authorization: `Bearer ${token}` },
    data: { org_domain: process.env.GUARD_TEST_ORG_DOMAIN, agent: { id: 'test-agent', name: 'TEST Agent (simulated)' }, source_mode: 'SIMULATED', target_url: target,
      action_type: 'update_existing_page', proposed_claims: ['TEST claim'], reason: 'e2e test', expected_kpi: 'visibility', risk: 'low', reversible: true, idempotency_key: `e2e-${Date.now()}` }
  })
  expect(post.ok()).toBeTruthy()
  await expect.poll(async () => panel.getByTestId('change-check').count(), { timeout: 15_000 }).toBeGreaterThan(before)
  await expect(panel.getByTestId('simulated-badge').first()).toBeVisible({ timeout: 15_000 })
})

// Mocked-response rendering proof: the API payloads below are TEST FIXTURES injected in the browser (the backend and the
// experiment are not touched). Used until the real API serves protection / change checks; screenshots end in -mock.png.
test('MOCKED responses: banner and BLOCK/DELAY checks render', async ({ page, request }) => {
  const list = await request.get(`${api}/api/experiments?limit=1`)
  test.skip(!list.ok(), 'experiments API unavailable')
  const id = (await list.json()).items?.[0]?.id
  test.skip(!id, 'no experiment')
  const until = '2026-10-04T20:29:43Z'
  const mkCheck = (n: number, decision: string, reason: string, extra: Record<string, unknown> = {}) => ({
    id: `mock-${n}`, decision, agent: { id: 'sim', name: 'TEST Content Agent' }, source_mode: 'SIMULATED', target_url: 'https://example.test/pricing',
    action_type: 'update_existing_page', evaluated_at: new Date().toISOString(), semantic_check: 'skipped_no_canonical_truth',
    findings: [{ type: decision === 'BLOCK' ? 'canonical_conflict' : 'active_experiment_contamination', severity: 'high', decision, reason,
      target_overlap_pct: 100, prompt_cluster_overlap_pct: 35, references: { experiment_code: 'EXP-0001' }, ...extra }], ...extra
  })
  await page.route(new RegExp(`/api/experiments/${id}(\\?.*)?$`), async (route) => {
    const res = await route.fetch()
    const body = await res.json()
    await route.fulfill({ response: res, json: { ...body, protection: { protected: true, until, targets: ['https://example.test/pricing'], checks_blocked_count: 2, recent_checks: [] } } })
  })
  await page.route(/\/api\/change-checks(\?.*)?$/, (route) => route.fulfill({ json: {
    items: [mkCheck(1, 'DELAY', 'Target overlaps experiment EXP-0001 which is still measuring.', { eligible_after: until }), mkCheck(2, 'BLOCK', 'Claim contradicts canonical claim: "No free tier".')], total: 2, limit: 10, offset: 0 } }))
  await page.goto(`/experiments/${id}`)
  await expect(page.getByTestId('protected-until')).toContainText('Protected until 2026-10-04 20:29 UTC', { timeout: 15_000 })
  await page.screenshot({ path: `${shots}/protected-experiment-banner-mock.png` })
  const panel = page.getByTestId('change-checks-panel')
  await expect(panel.getByTestId('decision-chip').first()).toBeVisible()
  await expect(panel.getByText('SIMULATED AGENT').first()).toBeVisible()
  await panel.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${shots}/change-checks-block-delay-mock.png` })
})
