#!/usr/bin/env bash
# Creates a throwaway DB, runs migrations, runs a minimal pytest module, drops DB.
# Requires: createdb/dropdb, DATABASE_URL host credentials, name must contain 'test' or 'smoke'.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DB_NAME="${AEO_FRESH_DB_NAME:-aeo_fresh_smoke_$$}"
export DATABASE_URL="postgresql+psycopg://aeo:aeo@localhost:5432/${DB_NAME}"

if [[ "$DB_NAME" != *test* && "$DB_NAME" != *smoke* ]]; then
  echo "Refusing: database name must contain 'test' or 'smoke'" >&2
  exit 2
fi

createdb -U aeo -h localhost "$DB_NAME" 2>/dev/null || createdb "$DB_NAME"
trap 'dropdb -U aeo -h localhost --if-exists "$DB_NAME" 2>/dev/null || dropdb --if-exists "$DB_NAME"' EXIT

cd "$ROOT/backend"
.venv/bin/alembic upgrade head
.venv/bin/python -m pytest -q tests/api/test_smoke.py::test_health_and_openapi
echo "fresh_db_smoke OK on $DB_NAME"
