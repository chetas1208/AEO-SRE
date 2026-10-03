# AEO SRE. Rootless docker users: export DOCKER_HOST=unix:///tmp/xdg-$$UID/docker.sock (see README).
VENV := backend/.venv/bin
.PHONY: train-ranker help install infra infra-down migrate migration dev dev-api dev-worker dev-web test test-web lint typecheck build-web seed-fixture eval eval-model-live verify verify-live profound-smoke mixpanel-smoke model-smoke ingest-live audit-db browser-smoke alembic-check verify-experiment eval-guard

help:
	@grep -E '^[a-z-]+:' Makefile | cut -d: -f1 | tr '\n' ' '; echo

install:
	cd backend && uv venv --python 3.12 && uv pip install -e ".[dev]"
	cd frontend && pnpm install

infra:
	docker compose up -d --wait

infra-down:
	docker compose down

migrate:
	cd backend && .venv/bin/alembic upgrade head

migration:  # usage: make migration m="add foo"
	cd backend && .venv/bin/alembic revision --autogenerate -m "$(m)"

dev: infra migrate
	@scripts/dev.sh

dev-api:
	cd backend && .venv/bin/uvicorn app.api.main:app --reload --port 8000

dev-worker:
	cd backend && .venv/bin/arq app.workers.worker.WorkerSettings

dev-web:
	cd frontend && pnpm dev

test:
	cd backend && .venv/bin/python -m pytest -q

test-web:
	cd frontend && pnpm test

lint:
	cd backend && .venv/bin/ruff check .

typecheck:
	cd frontend && pnpm typecheck

build-web:
	cd frontend && pnpm build

# DEV FIXTURE ONLY: writes clearly-labelled synthetic signals into your local DB. Never run against production.
seed-fixture:
	cd backend && .venv/bin/python ../scripts/seed_dev_fixture.py

# Deterministic decision regression. No Profound key and no model key. Writes experiments/evals/<timestamp>/.
eval:
	cd backend && .venv/bin/python -m evals

train-ranker:  # real training run on FEVER + VitaminC subsets (~8 min CPU); needs `uv pip install -e ".[ml]"`
	cd backend && PYTHONPATH=. .venv/bin/python -m ml.training.train_evidence_ranker --subset 40000

alembic-check:
	cd backend && .venv/bin/alembic check && .venv/bin/alembic current && .venv/bin/alembic heads

verify: lint test alembic-check typecheck test-web build-web eval

verify-live:
	@cd backend && .venv/bin/python -m app.devtools.profound_smoke && .venv/bin/python -m app.devtools.ingest_live

profound-smoke:
	cd backend && .venv/bin/python -m app.devtools.profound_smoke

mixpanel-smoke:
	cd backend && .venv/bin/python -m app.devtools.mixpanel_smoke

model-smoke:
	cd backend && .venv/bin/python -m app.devtools.model_smoke

eval-model-live:
	cd backend && .venv/bin/python -m app.devtools.eval_model_live

ingest-live:
	cd backend && .venv/bin/python -m app.devtools.ingest_live

audit-db:
	cd backend && .venv/bin/python -m app.devtools.audit_db

browser-smoke:
	bash scripts/browser_smoke.sh

verify-experiment:  # usage: make verify-experiment ID=<uuid>
	cd backend && .venv/bin/python -m app.devtools.experiment_status $(ID)

fresh-db-smoke:
	bash scripts/fresh_db_smoke.sh

# Change Guard deterministic scenario eval (SIMULATED agents, throwaway Postgres DB). Exit 2 = release_blocked.
eval-guard:
	cd backend && .venv/bin/python -m evals_guard

# Neo4j Aura connectivity + schema smoke (exit 0 ready / 1 degraded / 2 not configured / 3 auth failed). Never prints the password.
neo4j-smoke:
	cd backend && .venv/bin/python -m app.devtools.neo4j_smoke

# Discovery gap: canonical truth vs what AI engines say (Profound). Usage: make discovery-gap ORG=<uuid> [DRY=1]
discovery-gap:
	cd backend && .venv/bin/python -m app.devtools.discovery_gap_run --org $(ORG) $(if $(DRY),--dry-run,)

# Neo4j projection (N2). graph-audit is read-only (exit 1 on any Postgres<->Neo4j mismatch).
graph-audit:
	cd backend && .venv/bin/python -m app.graph.audit

graph-replay:  # history -> outbox (idempotent) -> project
	cd backend && .venv/bin/python -m app.graph.replay

# Clears ONLY the application graph (app="profound-change-guard"), replays everything, verifies. Needs GRAPH_REBUILD_ALLOW=1.
graph-rebuild:
	cd backend && .venv/bin/python -m app.graph.replay --rebuild --yes

graph-replay-test:
	cd backend && .venv/bin/python -m pytest -q tests/graph_projection
