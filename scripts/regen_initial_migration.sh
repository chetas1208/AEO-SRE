#!/usr/bin/env bash
# Pre-release only: rebuild the single initial Alembic revision from current models against a scratch DB, then
# verify `upgrade head` from empty and `alembic check`. Usage: scripts/regen_initial_migration.sh
set -euo pipefail
cd "$(dirname "$0")/../backend"
export DOCKER_HOST="${DOCKER_HOST:-unix:///tmp/xdg-$(id -u)/docker.sock}"
PG=$(docker ps --format '{{.Names}}' | grep postgres | head -1)
docker exec "$PG" psql -U aeo -d postgres -q -c "DROP DATABASE IF EXISTS aeo_mig" -c "CREATE DATABASE aeo_mig"
export DATABASE_URL=postgresql+psycopg://aeo:aeo@localhost:5432/aeo_mig
rm -f migrations/versions/0001_initial_schema.py
.venv/bin/alembic revision --autogenerate -m "initial schema" --rev-id 0001 >/dev/null 2>&1
.venv/bin/alembic upgrade head 2>&1 | tail -1
.venv/bin/alembic check 2>&1 | tail -1
