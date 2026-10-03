#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "[INFO] Restarting production backend..."
"${SCRIPT_DIR}/stop-backend-prod.sh"
sleep 2
"${SCRIPT_DIR}/start-backend-prod.sh"
