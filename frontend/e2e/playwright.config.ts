import { defineConfig, devices } from '@playwright/test'

const baseURL = process.env.PLAYWRIGHT_BASE_URL ?? 'http://127.0.0.1:3000'
const apiURL = process.env.PLAYWRIGHT_API_BASE_URL ?? process.env.NUXT_PUBLIC_API_BASE_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  testDir: '.',
  timeout: 60_000,
  retries: 0,
  reporter: [['list'], ['html', { open: 'never', outputFolder: '../test-artifacts/playwright-report' }]],
  outputDir: '../test-artifacts/playwright-results',
  use: {
    ...devices['Desktop Chrome'],
    baseURL,
    headless: true,
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
  metadata: { apiURL },
})
