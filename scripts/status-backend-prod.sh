#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/backend"
PID_FILE="${LOG_DIR}/backend.pid"

echo "=== Backend Process Status ==="
if [ -f "${PID_FILE}" ]; then
    PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${PID}" ] && kill -0 "${PID}" 2>/dev/null; then
        echo "Status: RUNNING"
        echo "PID:    ${PID}"
        echo "Port:   127.0.0.1:8000"
    else
        echo "Status: STOPPED (stale PID ${PID})"
    fi
else
    echo "Status: NOT RUNNING (no PID file)"
fi

echo ""
echo "=== Health Endpoint ==="
if curl -s -f http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
    echo "Health: OK"
    curl -s http://127.0.0.1:8000/api/health
    echo ""
else
    echo "Health: UNREACHABLE"
fi

echo ""
echo "=== Active Log ==="
if [ -L "${LOG_DIR}/latest.log" ]; then
    TARGET="$(readlink -f "${LOG_DIR}/latest.log")"
    echo "Latest log: ${TARGET}"
    echo "--- Last 20 lines ---"
    tail -n 20 "${TARGET}" 2>/dev/null || true
else
    echo "No latest.log symlink found."
fi
