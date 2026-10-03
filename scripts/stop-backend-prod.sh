#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/backend"
PID_FILE="${LOG_DIR}/backend.pid"
WORKER_PID_FILE="${LOG_DIR}/worker.pid"

stop_pid() {
    local target_pid="$1"
    local name="$2"

    if kill -0 "${target_pid}" 2>/dev/null; then
        echo "[INFO] Sending SIGTERM to ${name} (PID ${target_pid})..."
        kill "${target_pid}" 2>/dev/null || true
        for i in $(seq 1 10); do
            if ! kill -0 "${target_pid}" 2>/dev/null; then
                echo "[OK] ${name} stopped gracefully."
                return 0
            fi
            sleep 1
        done
        echo "[WARN] Force stopping ${name} (SIGKILL)..."
        kill -9 "${target_pid}" 2>/dev/null || true
    else
        echo "[INFO] ${name} (PID ${target_pid}) is not running."
    fi
}

# Stop API
if [ -f "${PID_FILE}" ]; then
    PID="$(cat "${PID_FILE}")"
    if [ -n "${PID}" ]; then
        stop_pid "${PID}" "Backend API"
    fi
    rm -f "${PID_FILE}"
else
    echo "[INFO] No backend.pid file found."
fi

# Stop Worker
if [ -f "${WORKER_PID_FILE}" ]; then
    WPID="$(cat "${WORKER_PID_FILE}")"
    if [ -n "${WPID}" ]; then
        stop_pid "${WPID}" "ARQ Worker"
    fi
    rm -f "${WORKER_PID_FILE}"
fi

# Also check for orphaned uvicorn on port 8000
PORT_PID=$(lsof -ti :8000 2>/dev/null || true)
if [ -n "${PORT_PID}" ]; then
    echo "[INFO] Cleaning up process on port 8000 (PID ${PORT_PID})..."
    kill -15 ${PORT_PID} 2>/dev/null || true
    sleep 1
fi

echo "[OK] Backend stop complete."
