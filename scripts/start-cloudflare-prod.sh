#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/cloudflare"
mkdir -p "${LOG_DIR}"

PID_FILE="${LOG_DIR}/cloudflared.pid"
URL_FILE="${LOG_DIR}/tunnel_url.txt"

# 1. Check if tunnel is already running
if [ -f "${PID_FILE}" ]; then
    EXISTING_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${EXISTING_PID}" ] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
        echo "[WARN] Cloudflare tunnel already running (PID: ${EXISTING_PID})."
        if [ -f "${URL_FILE}" ]; then
            echo "[INFO] Existing Tunnel URL: $(cat "${URL_FILE}")"
        fi
        exit 0
    else
        rm -f "${PID_FILE}"
    fi
fi

# 2. Check cloudflared binary
CLOUDFLARED_BIN="$(which cloudflared 2>/dev/null || echo "/home/923873155/.local/bin/cloudflared")"
if [ ! -x "${CLOUDFLARED_BIN}" ]; then
    echo "[ERROR] cloudflared not found or not executable" >&2
    exit 1
fi

STAMP="$(date +"%Y%m%d-%H%M%S")"
LOG_FILE="${LOG_DIR}/cloudflared-${STAMP}-p$$.log"

echo "[INFO] Launching Cloudflare Tunnel for http://127.0.0.1:8000..."
echo "[INFO] Tunnel log: ${LOG_FILE}"

# 3. Launch tunnel under nohup
setsid nohup "${CLOUDFLARED_BIN}" tunnel --url http://127.0.0.1:8000 \
    --no-autoupdate \
    < /dev/null >> "${LOG_FILE}" 2>&1 &

CF_PID=$!
disown ${CF_PID} 2>/dev/null || true
echo "${CF_PID}" > "${PID_FILE}"
ln -sfn "$(basename "${LOG_FILE}")" "${LOG_DIR}/latest.log"

echo "[INFO] cloudflared started with PID ${CF_PID}"
echo "[INFO] Waiting for public tunnel URL assignment..."

TUNNEL_URL=""
for i in $(seq 1 30); do
    if [ -f "${LOG_FILE}" ]; then
        FOUND_URL=$(grep -oE "https://[a-zA-Z0-9-]+\.trycloudflare\.com" "${LOG_FILE}" | head -n 1 || true)
        if [ -n "${FOUND_URL}" ]; then
            TUNNEL_URL="${FOUND_URL}"
            break
        fi
    fi
    sleep 1
done

if [ -z "${TUNNEL_URL}" ]; then
    echo "[ERROR] Could not extract tunnel URL within 30 seconds. Check log: ${LOG_FILE}"
    exit 1
fi

echo "${TUNNEL_URL}" > "${URL_FILE}"
echo "[OK] Public Cloudflare Tunnel URL: ${TUNNEL_URL}"

# 4. Verify tunnel endpoint
echo "[INFO] Testing public tunnel endpoint ${TUNNEL_URL}/api/health..."
HEALTH_OK=0
for i in $(seq 1 15); do
    if curl -s -f --doh-url https://cloudflare-dns.com/dns-query "${TUNNEL_URL}/api/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    elif curl -s -f "${TUNNEL_URL}/api/health" >/dev/null 2>&1; then
        HEALTH_OK=1
        break
    fi
    sleep 2
done

if [ "${HEALTH_OK}" -eq 1 ]; then
    echo "[OK] Tunnel verified healthy!"
    curl -s --doh-url https://cloudflare-dns.com/dns-query "${TUNNEL_URL}/api/health" 2>/dev/null || curl -s "${TUNNEL_URL}/api/health"
    echo ""
else
    echo "[WARN] Tunnel URL assigned, but /api/health did not respond immediately. Check logs."
fi
