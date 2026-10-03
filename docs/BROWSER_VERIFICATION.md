# Browser E2E verification

## Harness

Playwright smoke tests live in `frontend/e2e/smoke.spec.ts`.

## Commands

```bash
# Backend + frontend must be running (defaults: :8000 / :3000)
make browser-smoke
```

Install browsers once (no sudo required in most setups):

```bash
cd frontend && pnpm exec playwright install chromium
```

## If the environment lacks a browser runtime

The command exits with a clear message (`BROWSER_RUNTIME_UNAVAILABLE` / Playwright install hint). This is an **environment blocker**, not an application failure.

## What is covered

- Incidents list
- Incident detail + provenance badge (fixture must not show as live)
- Evidence graph or textual alternative
- Experiments page

Approval click flow can be added when a stable fixture intervention is available in CI.
