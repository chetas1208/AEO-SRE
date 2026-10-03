#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/cloudflare"
PID_FILE="${LOG_DIR}/cloudflared.pid"

if [ -f "${PID_FILE}" ]; then
    PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        echo "[INFO] Sending SIGTERM to cloudflared (PID ${PID})..."
        kill "${PID}" 2>/dev/null || true
        for i in $(seq 1 10); do
            if ! kill -0 "${PID}" 2>/dev/null; then
                echo "[OK] cloudflared stopped."
                break
            fi
            sleep 1
        done
        kill -9 "${PID}" 2>/dev/null || true
    fi
    rm -f "${PID_FILE}"
else
    echo "[INFO] No cloudflared.pid file found."
fi

# Fallback check for any dangling cloudflared processes running for 8000
DANGLING=$(pgrep -f "cloudflared tunnel --url http://127.0.0.1:8000" 2>/dev/null || true)
if [ -n "${DANGLING}" ]; then
    echo "[INFO] Stopping dangling cloudflared processes: ${DANGLING}"
    kill -15 ${DANGLING} 2>/dev/null || true
fi

echo "[OK] Cloudflare tunnel stopped."
