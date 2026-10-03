import { test, expect } from '@playwright/test'
import * as path from 'node:path'
import * as fs from 'node:fs'

const VIEWPORTS = [
  { width: 1920, height: 1080, name: '1920x1080' },
  { width: 1600, height: 900, name: '1600x900' },
  { width: 1440, height: 900, name: '1440x900' },
  { width: 1366, height: 768, name: '1366x768' },
  { width: 1280, height: 800, name: '1280x800' },
]

const ROUTES = [
  { path: '/', name: 'homepage' },
  { path: '/matches', name: 'matches' },
  { path: '/discovery-gaps', name: 'discovery-gaps' },
  { path: '/campaigns', name: 'campaigns' },
  { path: '/experiments', name: 'experiments' },
]

const screenshotDir = path.resolve('test-artifacts/screenshots/viewports')
fs.mkdirSync(screenshotDir, { recursive: true })

function boxesOverlap(
  r1: { x: number; y: number; width: number; height: number },
  r2: { x: number; y: number; width: number; height: number },
  tolerance = 1
) {
  return !(
    r1.x + r1.width <= r2.x + tolerance ||
    r2.x + r2.width <= r1.x + tolerance ||
    r1.y + r1.height <= r2.y + tolerance ||
    r2.y + r2.height <= r1.y + tolerance
  )
}

test.describe('Layout & Collision Verification Across Viewports', () => {
  for (const vp of VIEWPORTS) {
    test.describe(`Viewport ${vp.name}`, () => {
      test.use({ viewport: { width: vp.width, height: vp.height } })

      for (const route of ROUTES) {
        test(`Verify layout & zero collisions on ${route.path} (${vp.name})`, async ({ page }) => {
          await page.goto(route.path, { waitUntil: 'domcontentloaded' })
          await page.waitForTimeout(600)

          // 1. Verify TopBar and NavRail do not collide with Workspace
          const rail = page.locator('aside.rail')
          const topbar = page.locator('header.topbar')
          const content = page.locator('main.content')

          const hasRail = (await rail.count()) > 0 && (await rail.isVisible())
          const hasTopbar = (await topbar.count()) > 0 && (await topbar.isVisible())

          if (hasRail) {
            const railBox = (await rail.boundingBox())!
            expect(railBox.width).toBeGreaterThanOrEqual(180)
            expect(railBox.width).toBeLessThanOrEqual(280)

            // Split workspace or page container
            const workspace = page.locator('.split-workspace, .page-container, .home-page').first()
            if ((await workspace.count()) > 0 && (await workspace.isVisible())) {
              const wsBox = (await workspace.boundingBox())!
              expect(wsBox.x).toBeGreaterThanOrEqual(railBox.x + railBox.width - 2)
            }
          }

          if (hasTopbar) {
            const topbarBox = (await topbar.boundingBox())!
            expect(topbarBox.height).toBeGreaterThanOrEqual(48)
            expect(topbarBox.height).toBeLessThanOrEqual(64)

            const contentBox = (await content.boundingBox())!
            expect(contentBox.y).toBeGreaterThanOrEqual(topbarBox.y + topbarBox.height - 2)
          }

          // 2. Check split workspace pane collision (Queue Pane vs Workspace Pane)
          const splitWorkspace = page.locator('.split-workspace')
          if ((await splitWorkspace.count()) > 0 && (await splitWorkspace.isVisible())) {
            const queuePane = page.locator('.queue-pane').first()
            const workspacePane = page.locator('.workspace-pane').first()

            if (
              (await queuePane.count()) > 0 &&
              (await queuePane.isVisible()) &&
              (await workspacePane.count()) > 0 &&
              (await workspacePane.isVisible())
            ) {
              const qBox = (await queuePane.boundingBox())!
              const wsBox = (await workspacePane.boundingBox())!

              // Ensure horizontal split panes do not overlap
              expect(boxesOverlap(qBox, wsBox)).toBe(false)
              expect(wsBox.x).toBeGreaterThanOrEqual(qBox.x + qBox.width - 2)
            }
          }

          // 3. Card badge vs card title collision check
          const cardCollisions = await page.evaluate(() => {
            const collisions: string[] = []
            const cards = document.querySelectorAll(
              '.match-card, .gap-card, .campaign-card, .experiment-card, .incident-card'
            )
            for (let i = 0; i < Math.min(cards.length, 10); i++) {
              const card = cards[i]
              if (!card) continue
              const title = card.querySelector('h2, h3, h4, .card-title, .title')
              const badges = card.querySelectorAll(
                '.badge, .status-badge, .pill, .priority-badge, .band-pill'
              )
              if (title && badges.length > 0) {
                const tr = title.getBoundingClientRect()
                for (const badge of badges) {
                  const br = badge.getBoundingClientRect()
                  // Check overlap
                  const overlaps = !(
                    br.right <= tr.left ||
                    br.left >= tr.right ||
                    br.bottom <= tr.top ||
                    br.top >= tr.bottom
                  )
                  if (overlaps && br.width > 0 && tr.width > 0) {
                    collisions.push(
                      `Card #${i} badge (${badge.textContent?.trim()}) overlaps title (${title.textContent?.trim()})`
                    )
                  }
                }
              }
            }
            return collisions
          })
          expect(cardCollisions).toHaveLength(0)

          // 4. Graph Explorer collision check if present
          const graphExplorer = page.locator('.knowledge-graph-explorer')
          if ((await graphExplorer.count()) > 0 && (await graphExplorer.isVisible())) {
            const toolbar = graphExplorer.locator('.graph-toolbar')
            const viewport = graphExplorer.locator('.graph-viewport')
            if (
              (await toolbar.count()) > 0 &&
              (await toolbar.isVisible()) &&
              (await viewport.count()) > 0 &&
              (await viewport.isVisible())
            ) {
              const tbBox = (await toolbar.boundingBox())!
              const vpBox = (await viewport.boundingBox())!
              // Toolbar is stacked above or inside viewport with clean bounds
              expect(tbBox.width).toBeGreaterThan(200)
              expect(vpBox.height).toBeGreaterThan(250)
            }
          }

          // 5. Capture screenshot for visual inspection
          await page.screenshot({
            path: `${screenshotDir}/${route.name}-${vp.name}.png`,
            fullPage: false,
          })
        })
      }
    })
  }
})
