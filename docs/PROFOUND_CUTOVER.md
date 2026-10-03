# Profound live cutover

## Prerequisites

- Root `.env` with `PROFOUND_API_KEY` (optional `PROFOUND_BASE_URL`, default `https://api.tryprofound.com`)
- Postgres + Redis running
- Backend dependencies installed (`make install`)

## Steps

1. Set the key in `.env` (never commit it).
2. Restart API and worker so settings reload.
3. Smoke test (read-only probes, redacted output):

   ```bash
   make profound-smoke
   ```

4. Ingest one organization (same path as the worker):

   ```bash
   make ingest-live
   # or: cd backend && .venv/bin/python -m app.devtools.ingest_live --org-id <uuid>
   ```

5. Inspect normalized signals and run detection (existing API or worker cron).

6. Confirm `/api/system/capabilities` shows Profound surfaces with live verification where probes succeeded.

## Expected states without a key

- `/api/health`: `profound: not_configured`, overall API healthy
- Ingest returns `status: unavailable`, reason `not_configured`, **no fixture fallback**

## Expected states with a bad key

- Capability probes: `auth_failed` or degraded; application stays up
