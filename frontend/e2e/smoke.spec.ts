/**
 * Narrow E2E against a running backend + frontend (fixture data OK).
 * Requires: pnpm exec playwright install chromium
 */
import { test, expect } from '@playwright/test'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${api}/api/health`)
  if (!health.ok()) {
    test.skip(true, `Backend not reachable at ${api}`)
  }
})

test('incidents list loads', async ({ page }) => {
  await page.goto('/incidents')
  await expect(page.getByRole('heading', { name: /incidents/i })).toBeVisible({ timeout: 15_000 })
})

test('incident detail and provenance badge', async ({ page, request }) => {
  const list = await request.get(`${api}/api/incidents?limit=1`)
  test.skip(!list.ok(), 'incidents API unavailable')
  const body = await list.json()
  const id = body.items?.[0]?.id
  test.skip(!id, 'no incidents in database')
  await page.goto(`/incidents/${id}`)
  await expect(page.getByTestId('incident-title')).toBeVisible({ timeout: 15_000 })
  const badge = page.getByTestId('provenance-badge')
  if (await badge.count()) {
    await expect(badge).not.toHaveText(/live profound/i)
  }
})

test('evidence graph or textual fallback', async ({ page, request }) => {
  const list = await request.get(`${api}/api/incidents?limit=1`)
  test.skip(!list.ok(), 'incidents API unavailable')
  const id = (await list.json()).items?.[0]?.id
  test.skip(!id, 'no incidents')
  await page.goto(`/incidents/${id}`)
  const graph = page.locator('[data-testid="evidence-graph"]')
  await expect(graph).toBeVisible({ timeout: 15_000 })
})

test('experiments page loads', async ({ page }) => {
  await page.goto('/experiments')
  await expect(page.getByRole('heading', { name: 'Experiments', exact: true })).toBeVisible({ timeout: 15_000 })
})

