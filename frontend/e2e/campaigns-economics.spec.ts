import { test, expect } from '@playwright/test'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${api}/api/health`)
  if (!health.ok()) test.skip(true, `Backend not reachable at ${api}`)
})

function assertCampaignLedger(c: Record<string, unknown>) {
  const id = String(c.id ?? 'unknown')
  const cost = Number(c.total_cost ?? c.totalCost)
  const ret = Number(c.attributed_return ?? c.attributedReturn)
  const roi = Number(c.roi ?? c.roi_pct ?? c.roiPct)
  expect(cost, `${id} total_cost`).toBeGreaterThan(0)
  expect(ret, `${id} attributed_return`).toBeGreaterThan(0)
  expect(roi, `${id} roi`).toBeGreaterThan(0)
}

test('Every campaign exposes non-zero cost, return, and ROI', async ({ request }) => {
  const res = await request.get(`${api}/api/campaigns`)
  expect(res.ok(), await res.text()).toBeTruthy()
  const body = await res.json()
  const campaigns = body.campaigns as Array<Record<string, unknown>>
  expect(campaigns.length).toBeGreaterThan(0)
  for (const c of campaigns) assertCampaignLedger(c)
})

test('Control plane aggregates non-zero spend and return across all campaigns', async ({ request }) => {
  const res = await request.get(`${api}/api/control-plane`)
  expect(res.ok(), await res.text()).toBeTruthy()
  const body = await res.json()
  const summary = body.summary ?? {}
  expect(Number(summary.total_spend ?? summary.totalSpend)).toBeGreaterThan(0)
  expect(Number(summary.attributed_return ?? summary.attributedReturn)).toBeGreaterThan(0)

  const campaigns = (body.campaigns ?? []) as Array<Record<string, unknown>>
  expect(campaigns.length).toBeGreaterThan(0)
  for (const c of campaigns) assertCampaignLedger(c)
})

test.describe('browser', () => {
  test.beforeEach(({ browserName }, testInfo) => {
    test.skip(
      process.env.PLAYWRIGHT_API_ONLY === '1',
      'Browser UI checks skipped (PLAYWRIGHT_API_ONLY=1). Run: sudo npx playwright install-deps && npm run test:e2e:ui'
    )
    testInfo.annotations.push({ type: 'browser', description: browserName })
  })

  test('Campaigns page lists initiatives with dollar amounts', async ({ page, request }) => {
    const res = await request.get(`${api}/api/campaigns`)
    const body = await res.json()
    const first = body.campaigns?.[0] as { name?: string; total_cost?: number } | undefined
    test.skip(!first?.name, 'No campaigns from API')

    await page.goto('/campaigns')
    await expect(page.getByText(first!.name!).first()).toBeVisible({ timeout: 15_000 })

    const cost = Number(first!.total_cost)
    expect(cost).toBeGreaterThan(0)
    const costLabel = cost >= 1000 ? `${(cost / 1000).toFixed(1)}K` : String(Math.round(cost))
    await expect(page.getByText(new RegExp(costLabel.replace('.', '\\.'))).first()).toBeVisible({ timeout: 10_000 })
  })

  test('Control plane campaign economics tab shows non-zero totals', async ({ page, request }) => {
    const res = await request.get(`${api}/api/control-plane`)
    expect(res.ok()).toBeTruthy()

    await page.goto('/')
    const campaignsTab = page.getByRole('tab', { name: /Campaign Economics/i })
    await expect(campaignsTab).toBeVisible({ timeout: 15_000 })
    await campaignsTab.click()
    await expect(page.getByRole('columnheader', { name: 'Total Cost' })).toBeVisible()
    const table = page.locator('table').filter({ has: page.getByRole('columnheader', { name: 'Total Cost' }) })
    await expect(table.getByText(/\$0(\.00)?/)).toHaveCount(0)
  })
})
