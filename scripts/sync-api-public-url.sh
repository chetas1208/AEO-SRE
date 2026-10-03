#!/usr/bin/env bash
# Writes API_PUBLIC_URL / proxy vars in root .env from logs/cloudflare/tunnel_url.txt (after start-cloudflare-prod.sh).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
URL_FILE="${ROOT}/logs/cloudflare/tunnel_url.txt"
ENV_FILE="${ROOT}/.env"

if [[ ! -f "${URL_FILE}" ]]; then
  echo "[ERROR] Missing ${URL_FILE}. Run scripts/start-cloudflare-prod.sh first." >&2
  exit 1
fi
TUNNEL="$(tr -d '[:space:]' < "${URL_FILE}")"
if [[ ! "${TUNNEL}" =~ ^https:// ]]; then
  echo "[ERROR] Invalid tunnel URL in ${URL_FILE}: ${TUNNEL}" >&2
  exit 1
fi

touch "${ENV_FILE}"
upsert() {
  local key="$1" val="$2"
  if grep -q "^${key}=" "${ENV_FILE}" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${val}|" "${ENV_FILE}"
  else
    printf '\n%s=%s\n' "${key}" "${val}" >> "${ENV_FILE}"
  fi
}

upsert "API_PUBLIC_URL" "${TUNNEL}"
upsert "BACKEND_BASE_URL" "${TUNNEL}"
upsert "NUXT_BACKEND_PROXY_URL" "${TUNNEL}"
upsert "NUXT_PUBLIC_API_SAME_ORIGIN" "1"
upsert "NUXT_PUBLIC_API_BASE_URL" ""

echo "[OK] .env synced: API_PUBLIC_URL=${TUNNEL}"
echo "     Vercel: npx vercel env add API_PUBLIC_URL production  (and NUXT_BACKEND_PROXY_URL) then redeploy."
