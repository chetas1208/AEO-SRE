#!/usr/bin/env bash
# Start rootless dockerd (no systemd) and Postgres/Redis. Idempotent. Host permission workaround only.
set -euo pipefail
export XDG_RUNTIME_DIR="/tmp/xdg-$(id -u)"
mkdir -p "$XDG_RUNTIME_DIR" && chmod 700 "$XDG_RUNTIME_DIR"
export DOCKER_HOST="unix://$XDG_RUNTIME_DIR/docker.sock"
if ! docker info >/dev/null 2>&1; then
  DOCKER_IGNORE_BR_NETFILTER_ERROR=1 setsid nohup dockerd-rootless.sh >/tmp/dockerd-rootless.log 2>&1 &
  for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done
fi
docker compose up -d
echo "export DOCKER_HOST=$DOCKER_HOST   # use in your shell"
