/**
 * Real pointer clicks only (no force, no el.click()): proves nothing (nav rail, ambient layer) intercepts content.
 * Read-only against any stack. Mutating steps (Approve, Mark as executed) run only with PLAYWRIGHT_SCRATCH=1 against a
 * scratch stack that has an incident awaiting approval; never run those against the dev fixture.
 */
import { test, expect } from '@playwright/test'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'
const scratch = process.env.PLAYWRIGHT_SCRATCH === '1'

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${api}/api/health`)
  if (!health.ok()) test.skip(true, `Backend not reachable at ${api}`)
})

test('nav rail occupies only its own column', async ({ page }) => {
  await page.goto('/incidents')
  const rail = page.locator('aside.rail')
  await expect(rail).toBeVisible()
  const box = (await rail.boundingBox())!
  expect(box.width).toBeLessThan(300)
  await page.getByTestId('incident-card').first().waitFor()
  const hit = await page.evaluate(() => {
    const el = document.querySelector('[data-testid="incident-card"]')!.getBoundingClientRect()
    return document.elementFromPoint(el.x + el.width / 2, el.y + el.height / 2)?.closest('aside.rail') !== null
  })
  expect(hit).toBe(false)
})

test('real clicks: incident card, Action tab, approve dialog', async ({ page }) => {
  await page.goto('/incidents')
  await page.getByTestId('incident-card').first().click()
  await expect(page.getByTestId('incident-title')).toBeVisible()
  await page.getByRole('tab', { name: /action/i }).click()
  await expect(page.getByTestId('action-panel')).toBeVisible()
  const open = page.getByTestId('approve-open')
  if (await open.count() && await open.isEnabled()) {
    await open.click()
    await expect(page.getByTestId('approval-explainer')).toBeVisible()
    await page.getByRole('button', { name: 'Cancel' }).last().click()
    await expect(page.getByTestId('approval-explainer')).toBeHidden()
  }
})

test('real clicks: experiment queue cards', async ({ page }) => {
  // /experiments redirects to the first experiment; the queue of cards is the list UI.
  await page.goto('/experiments')
  const empty = await page.getByText('No experiments recorded').first().isVisible().catch(() => false)
  test.skip(empty, 'no experiments')
  const cards = page.locator('aside[aria-label="Recent experiments queue"] a[href^="/experiments/"]')
  await expect(cards.first()).toBeVisible()
  const n = await cards.count()
  await cards.nth(n > 1 ? 1 : 0).click()
  await expect(page).toHaveURL(/\/experiments\/[^/]+$/)
})

test('real clicks: approve then Mark as executed (scratch only)', async ({ page, request }) => {
  test.skip(!scratch, 'mutating flow: set PLAYWRIGHT_SCRATCH=1 on a scratch stack')
  const items = (await (await request.get(`${api}/api/incidents?limit=50`)).json()).items as Array<{ id: string; state: string }>
  const target = items.find((i) => ['intervention_proposed', 'awaiting_approval'].includes(i.state))
  test.skip(!target, 'no incident awaiting approval')
  await page.goto(`/incidents/${target!.id}`)
  await page.getByRole('tab', { name: /action/i }).click()
  await page.getByTestId('approve-open').click()
  await page.getByTestId('approve-submit').click()
  await page.getByRole('button', { name: 'Close' }).click()
  await expect(page.getByTestId('mark-executed-form')).toBeVisible({ timeout: 15_000 })
  await page.getByTestId('mark-executed-submit').click()
  await expect(page.getByTestId('mark-executed-form')).toBeHidden({ timeout: 15_000 })
})
