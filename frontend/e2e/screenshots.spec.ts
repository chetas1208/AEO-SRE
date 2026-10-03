import { test, expect } from '@playwright/test'
import * as path from 'node:path'
import * as fs from 'node:fs'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'
const screenshotDir = path.resolve('test-artifacts/screenshots')
fs.mkdirSync(screenshotDir, { recursive: true })

test('screenshot: change-guard-default', async ({ page }) => {
  await page.goto('/incidents')
  await expect(page.getByRole('heading', { name: /incidents|change guard/i })).toBeVisible({ timeout: 25_000 })
  await page.screenshot({ path: `${screenshotDir}/change-guard-default.png`, fullPage: true })
})

test('screenshot: change-conflict-selected', async ({ page, request }) => {
  const list = await request.get(`${api}/api/incidents?limit=1`)
  const id = (await list.json()).items?.[0]?.id
  if (!id) return
  await page.goto(`/incidents/${id}`)
  await expect(page.getByTestId('incident-title')).toBeVisible({ timeout: 25_000 })
  await page.screenshot({ path: `${screenshotDir}/change-conflict-selected.png`, fullPage: true })
})

test('screenshot: change-topology', async ({ page, request }) => {
  const list = await request.get(`${api}/api/incidents?limit=1`)
  const id = (await list.json()).items?.[0]?.id
  if (!id) return
  await page.goto(`/incidents/${id}`)
  const graph = page.locator('[data-testid="evidence-graph"]')
  await expect(graph).toBeVisible({ timeout: 25_000 })
  await graph.scrollIntoViewIfNeeded()
  await page.waitForTimeout(1000)
  await graph.screenshot({ path: `${screenshotDir}/change-topology.png` })
})

test('screenshot: canonical-truth-conflict', async ({ page }) => {
  await page.goto('/settings')
  const canonicalSection = page.getByTestId('canonical-truth')
  await expect(canonicalSection).toBeVisible({ timeout: 25_000 })
  await canonicalSection.scrollIntoViewIfNeeded()
  await page.screenshot({ path: `${screenshotDir}/canonical-truth-conflict.png`, fullPage: true })
})

test('screenshot: experiments-protected', async ({ page, request }) => {
  const expList = await request.get(`${api}/api/experiments?limit=1`)
  const expId = (await expList.json()).items?.[0]?.id
  if (!expId) return
  await page.goto(`/experiments/${expId}`)
  await expect(page.getByRole('heading', { name: /EXP-/i }).or(page.locator('.exp-header-code'))).toBeVisible({ timeout: 25_000 })
  await page.screenshot({ path: `${screenshotDir}/experiments-protected.png`, fullPage: true })
})
