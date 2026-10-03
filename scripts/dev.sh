#!/usr/bin/env bash
# Starts API, worker and web together; Ctrl-C stops all. Infra + migrations are handled by `make dev`.
set -euo pipefail
cd "$(dirname "$0")/.."
trap 'kill 0' EXIT INT TERM
(cd backend && .venv/bin/uvicorn app.api.main:app --reload --port 8000) &
(cd backend && .venv/bin/arq app.workers.worker.WorkerSettings) &
(cd frontend && pnpm dev) &
wait
