# Mixpanel event catalog

**Status:** `MIXPANEL LIVE BLOCKED` — no service-account credentials are present in the workspace `.env` yet.

When configured, populate this file from live discovery:

```bash
make mixpanel-smoke          # auth + tiny sample
cd backend && .venv/bin/python -m app.devtools.mixpanel_events
```

## Access method (target)

| Item | Value |
|------|--------|
| API | Mixpanel **Service Account** (read-only) |
| Auth | HTTP Basic (`MIXPANEL_SERVICE_ACCOUNT_USERNAME` + secret) |
| Discovery | `GET /api/2.0/events/names` |
| Incremental ingest | `GET https://data.mixpanel.com/api/2.0/export/` (JSONL, polling) |
| Sync mode | **Near real-time** (worker poll; not streaming) |

## Event catalog (from live project)

_No live events sampled yet._ After credentials are added, this section will list:

| Event name | Approx. frequency | Notes |
|------------|-------------------|--------|
| _(pending discovery)_ | | |

## Correlation properties observed

_(None until live schema discovery completes.)_

Expected keys (if present in Profound’s project): `campaign_id`, `experiment_id`, `agent_run_id`, `correlation_id`, UTM fields.

## Honesty rules

- Events ingested here are **telemetry observations** only; Postgres domain state is unchanged unless an explicit pipeline links them.
- Temporal correlation is labelled **LOW** confidence in the UI/API.
- Do not mark fixtures as **LIVE MIXPANEL**.
