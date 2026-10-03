#!/usr/bin/env bash
# Fail if a production-config frontend build embeds browser loopback API URLs.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="${ROOT}/frontend/.output/public"
API_BASE="${NUXT_PUBLIC_API_BASE_URL:-https://build-verify.invalid}"
cd "${ROOT}/frontend"
NUXT_PUBLIC_API_BASE_URL="${API_BASE}" pnpm exec nuxt build >/dev/null
if rg -q 'localhost:8000|127\.0\.0\.1:8000' "${OUT}" 2>/dev/null; then
  echo "FAIL: loopback API URL found in production bundle under ${OUT}" >&2
  rg 'localhost:8000|127\.0\.0\.1:8000' "${OUT}" | head -5 >&2
  exit 1
fi
echo "OK: no loopback API URL in bundle (api base ${API_BASE})"
