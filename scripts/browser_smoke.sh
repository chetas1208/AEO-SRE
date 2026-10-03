#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${PLAYWRIGHT_API_BASE_URL:-${NUXT_PUBLIC_API_BASE_URL:-http://127.0.0.1:8000}}"
WEB="${PLAYWRIGHT_BASE_URL:-http://127.0.0.1:3000}"

if ! curl -sf "$API/api/health" >/dev/null; then
  echo "BACKEND_UNREACHABLE: start API at $API" >&2
  exit 2
fi
if ! curl -sf "$WEB/" >/dev/null; then
  echo "FRONTEND_UNREACHABLE: start Nuxt at $WEB" >&2
  exit 2
fi

cd "$ROOT/frontend"
if ! pnpm exec playwright --version >/dev/null 2>&1; then
  echo "BROWSER_RUNTIME_UNAVAILABLE: run 'cd frontend && pnpm install && pnpm exec playwright install chromium'" >&2
  exit 3
fi

if ! pnpm exec playwright test -c e2e/playwright.config.ts "$@"; then
  code=$?
  if pnpm exec playwright test -c e2e/playwright.config.ts --list 2>&1 | grep -qi 'Executable doesn'; then
    echo "BROWSER_RUNTIME_UNAVAILABLE: install Chromium via playwright install" >&2
    exit 3
  fi
  exit "$code"
fi
