import { test, expect } from '@playwright/test'

const api = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'

test.beforeAll(async ({ request }) => {
  const health = await request.get(`${api}/api/health`)
  if (!health.ok()) test.skip(true, `Backend not reachable at ${api}`)
})

test('Flight Deck renders heading, KPI strip, and operational tabs on /', async ({ page }) => {
  await page.goto('/')

  await expect(page.getByRole('heading', { name: 'Control Plane' })).toBeVisible({ timeout: 15_000 })

  await expect(page.getByText('Active Agents', { exact: true })).toBeVisible()
  await expect(page.getByText('Running Campaigns', { exact: true })).toBeVisible()
  await expect(page.getByText('Model Cost Today', { exact: true })).toBeVisible()
  await expect(page.getByText('Attributed Return', { exact: true })).toBeVisible()

  const campaignsTab = page.getByRole('tab', { name: /Campaign Economics/i })
  await expect(campaignsTab).toBeVisible()
  await campaignsTab.click()
  await expect(page.getByRole('columnheader', { name: 'Total Cost' })).toBeVisible()

  const decisionsTab = page.getByRole('tab', { name: /Policy Decisions/i })
  await decisionsTab.click()
  await expect(page.getByRole('columnheader', { name: 'Decision Action' })).toBeVisible()

  const experimentsTab = page.getByRole('tab', { name: /Protected Experiments/i })
  await experimentsTab.click()
  await expect(page.getByRole('columnheader', { name: 'Change Guard' })).toBeVisible()
})

test('+ New Experiment drawer UI; create via internal sandbox API (run_mode test)', async ({ page, request }) => {
  await page.goto('/')

  const newExpBtn = page.getByRole('button', { name: '+ New Experiment' })
  await expect(newExpBtn).toBeVisible()
  await newExpBtn.click()

  const dialog = page.getByRole('dialog', { name: 'Create New Experiment' })
  await expect(dialog).toBeVisible()
  await expect(page.getByRole('heading', { name: 'Create Real Experiment' })).toBeVisible()
  await expect(page.getByText('Step 1 of 3')).toBeVisible()

  const uniqueName = `E2E Test Experiment ${Date.now()}`
  const uniqueUrl = `https://profound.academy/test-url-${Date.now()}`
  await page.locator('#exp-name').fill(uniqueName)
  await page.locator('#exp-hypothesis').fill('Testing real causal loop verification and Change Guard protection')
  await page.locator('#exp-url').fill(uniqueUrl)

  await page.getByRole('button', { name: /Next Step/i }).click()
  await expect(page.getByText('Step 2 of 3')).toBeVisible()
  await page.getByRole('button', { name: /Next Step/i }).click()
  await expect(page.getByText('Step 3 of 3')).toBeVisible()
  await expect(page.getByText(uniqueName)).toBeVisible()

  // UI stays live-facing; CI creates through internal sandbox mode (not exposed in the drawer).
  const createRes = await request.post(`${api}/api/experiments`, {
    data: {
      name: uniqueName,
      hypothesis: 'Testing real causal loop verification and Change Guard protection',
      target_url: uniqueUrl,
      run_mode: 'test',
      auto_activate: true,
    },
  })
  expect(createRes.ok(), await createRes.text()).toBeTruthy()
  const body = await createRes.json()
  const expId = body.id as string

  await page.goto(`/experiments/${expId}`)
  await expect(page).toHaveURL(new RegExp(`/experiments/${expId}`), { timeout: 15_000 })
  await expect(page.getByText(uniqueName).first()).toBeVisible({ timeout: 15_000 })
})

test('Contextual Experiment button on /campaigns opens prefilled drawer', async ({ page }) => {
  await page.goto('/campaigns')

  const testExpBtn = page.getByRole('button', { name: /Test in Experiments Engine/i })
  if (await testExpBtn.count()) {
    await testExpBtn.first().click()
    const dialog = page.getByRole('dialog', { name: 'Create New Experiment' })
    await expect(dialog).toBeVisible()
    await expect(page.getByText('Trigger: Campaign')).toBeVisible()
  }
})
