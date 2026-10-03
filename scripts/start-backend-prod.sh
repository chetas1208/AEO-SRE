#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
LOG_DIR="${ROOT_DIR}/logs/backend"
mkdir -p "${LOG_DIR}"

PID_FILE="${LOG_DIR}/backend.pid"
WORKER_PID_FILE="${LOG_DIR}/worker.pid"

# 1. Check if backend is already running
if [ -f "${PID_FILE}" ]; then
    EXISTING_PID="$(cat "${PID_FILE}" 2>/dev/null || true)"
    if [ -n "${EXISTING_PID}" ] && kill -0 "${EXISTING_PID}" 2>/dev/null; then
        echo "[WARN] Backend is already running with PID ${EXISTING_PID}."
        echo "Use scripts/status-backend-prod.sh or scripts/restart-backend-prod.sh."
        exit 0
    else
        rm -f "${PID_FILE}"
    fi
fi

# 2. Prepare timestamped logfile
STAMP="$(date +"%Y%m%d-%H%M%S")"
LOG_FILE="${LOG_DIR}/backend-${STAMP}-p$$.log"
WORKER_LOG_FILE="${LOG_DIR}/worker-${STAMP}-p$$.log"

echo "[INFO] Starting backend at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
echo "[INFO] Logfile: ${LOG_FILE}"

cd "${ROOT_DIR}/backend"

# Ensure venv python exists
PYTHON_BIN="${ROOT_DIR}/backend/.venv/bin/python"
UVICORN_BIN="${ROOT_DIR}/backend/.venv/bin/uvicorn"
ARQ_BIN="${ROOT_DIR}/backend/.venv/bin/arq"

if [ ! -x "${UVICORN_BIN}" ]; then
    echo "[ERROR] Virtualenv uvicorn not found at ${UVICORN_BIN}" >&2
    exit 1
fi

# 3. Launch FastAPI backend under nohup
setsid nohup "${UVICORN_BIN}" app.api.main:app \
    --host 127.0.0.1 \
    --port 8000 \
    --workers 1 \
    --log-level info \
    < /dev/null >> "${LOG_FILE}" 2>&1 &

API_PID=$!
disown ${API_PID} 2>/dev/null || true
echo "${API_PID}" > "${PID_FILE}"
ln -sfn "$(basename "${LOG_FILE}")" "${LOG_DIR}/latest.log"

# 4. Launch ARQ worker under nohup
if [ -x "${ARQ_BIN}" ]; then
    setsid nohup "${ARQ_BIN}" app.workers.worker.WorkerSettings \
        < /dev/null >> "${WORKER_LOG_FILE}" 2>&1 &
    WORKER_PID=$!
    disown ${WORKER_PID} 2>/dev/null || true
    echo "${WORKER_PID}" > "${WORKER_PID_FILE}"
    ln -sfn "$(basename "${WORKER_LOG_FILE}")" "${LOG_DIR}/latest-worker.log"
    echo "[INFO] ARQ worker started (PID: ${WORKER_PID})"
fi

echo "[INFO] Backend API started (PID: ${API_PID})"

# 5. Health check poll (up to 15 seconds)
echo "[INFO] Verifying backend health on 127.0.0.1:8000/api/health..."
HEALTHY=0
for i in $(seq 1 15); do
    if curl -s -f http://127.0.0.1:8000/api/health >/dev/null 2>&1; then
        HEALTHY=1
        break
    fi
    sleep 1
done

if [ "${HEALTHY}" -eq 1 ]; then
    echo "[OK] Backend health check PASSED!"
    curl -s http://127.0.0.1:8000/api/health
    echo ""
else
    echo "[ERROR] Backend health check failed after 15s. Check log:"
    tail -n 25 "${LOG_FILE}"
    exit 1
fi
