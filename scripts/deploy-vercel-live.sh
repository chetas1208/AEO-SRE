#!/usr/bin/env bash
# Keep local backend + tunnel up, sync tunnel URL into Vercel env, production deploy.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

"${ROOT}/scripts/restart-backend-prod.sh"

if [[ ! -f "${ROOT}/logs/cloudflare/tunnel_url.txt" ]]; then
  "${ROOT}/scripts/start-cloudflare-prod.sh"
fi
"${ROOT}/scripts/sync-api-public-url.sh"

TUNNEL="$(tr -d '[:space:]' < "${ROOT}/logs/cloudflare/tunnel_url.txt")"
echo "[INFO] Tunnel: ${TUNNEL}"

cd "${ROOT}/frontend"
vercel_env_set() {
  local name="$1" value="$2"
  vercel env rm "${name}" production -y >/dev/null 2>&1 || true
  printf '%s' "${value}" | vercel env add "${name}" production
}

vercel_env_set "API_PUBLIC_URL" "${TUNNEL}"
vercel_env_set "NUXT_BACKEND_PROXY_URL" "${TUNNEL}"
vercel_env_set "NUXT_PUBLIC_API_SAME_ORIGIN" "1"
vercel env rm "NUXT_PUBLIC_API_BASE_URL" production -y >/dev/null 2>&1 || true

echo "[INFO] Deploying frontend to Vercel production..."
vercel deploy --prod --yes

echo "[OK] Production: https://aeo-sre.vercel.app"
curl -sS -m 15 "https://aeo-sre.vercel.app/api/health" | head -c 200
echo
