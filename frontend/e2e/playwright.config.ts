import fs from 'node:fs'
import { defineConfig, devices } from '@playwright/test'

function resolveChromiumExecutable(): string | undefined {
  if (process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH) {
    return process.env.PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH
  }
  const home = process.env.HOME || ''
  const candidates = fs
    .readdirSync(`${home}/.cache/ms-playwright`, { withFileTypes: true })
    .filter(d => d.isDirectory() && d.name.startsWith('chromium-') && !d.name.includes('headless'))
    .map(d => `${home}/.cache/ms-playwright/${d.name}/chrome-linux64/chrome`)
    .concat([`${home}/.cache/ms-playwright/chromium-1161/chrome-linux/chrome`])
  return candidates.find(p => fs.existsSync(p))
}

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
  projects: [
    {
      name: 'firefox',
      use: { ...devices['Desktop Firefox'] },
    },
    {
      name: 'chromium',
      use: {
        ...devices['Desktop Chrome'],
        launchOptions: {
          ...(resolveChromiumExecutable()
            ? { executablePath: resolveChromiumExecutable(), args: ['--no-sandbox', '--disable-dev-shm-usage'] }
            : {}),
        },
      },
    },
  ],
  metadata: { apiURL },
})
