# AEO SRE / AgentMatch — Production Deployment Architecture & Runbook

This document describes the public deployment architecture, process management, logging retention policy, health checks, reboot recovery, and rollback procedures for the AEO SRE / AgentMatch control plane.

---

## 1. System Architecture

```text
                           INTERNET
                              │
                              ▼
                     VERCEL FRONTEND
               Nuxt 4 / Vue 3 / Three.js
               (Root directory: frontend)
                              │
                              │ HTTPS (API & SSE requests)
                              ▼
                   CLOUDFLARE TUNNEL
     https://destinations-cams-easily-comparing.trycloudflare.com
                              │
                              │ Local QUIC/HTTP2 tunnel
                              ▼
                       HOST SERVER
                      127.0.0.1:8000
                              │
                              ▼
                    nohup FastAPI BACKEND
                 (app.api.main:app + ARQ)
                              │
            ┌─────────────────┼─────────────────┐
            ▼                 ▼                 ▼
        PostgreSQL          Redis           Neo4j Aura
     (System & Guard)   (Queue & Lock)  (Graph Projection)
```

---

## 2. Component Configuration

| Component | Target / Host | Ingress / Endpoint | Status |
| :--- | :--- | :--- | :--- |
| **Frontend** | Vercel | `https://aeo-sre.vercel.app` (or assigned project URL) | Production |
| **API Gateway** | Cloudflare Tunnel | `https://destinations-cams-easily-comparing.trycloudflare.com` | Live & Verified |
| **Backend API** | Host Server | `127.0.0.1:8000` (FastAPI / Uvicorn) | Active (nohup) |
| **Worker Engine** | Host Server | ARQ worker (`app.workers.worker.WorkerSettings`) | Active (nohup) |
| **Database** | Host Server | PostgreSQL 16 (Alembic Head: 0006) | Healthy |
| **Cache/Queue** | Host Server | Redis 7.2 | Healthy |
| **Knowledge Graph**| Cloud Hosted | Neo4j Aura Enterprise (`5.27-aura`) | Connected & Ready |
| **Profound Connector**| External API | Live Connector (`api.profound.com`) | Connected & Live |
| **Model Runtime** | External API | Anthropic (`claude-haiku-4-5` / `claude-sonnet-4-6`) | Configured & Ready |

---

## 3. Operational Scripts & Process Management

All operational management scripts are located in `scripts/` and must be run from the repository root:

### Backend Management

```bash
# Check status, PID, port, health check, and recent logs
scripts/status-backend-prod.sh

# Start backend API and ARQ background worker under nohup
scripts/start-backend-prod.sh

# Stop backend API and worker gracefully via PID files (SIGTERM, fallback SIGKILL)
scripts/stop-backend-prod.sh

# Restart backend cleanly (stop -> wait -> start with fresh unique log)
scripts/restart-backend-prod.sh
```

### Cloudflare Tunnel Management

```bash
# Launch Cloudflare tunnel with append-only logs and automatic URL extraction
scripts/start-cloudflare-prod.sh

# Stop Cloudflare tunnel gracefully
scripts/stop-cloudflare-prod.sh
```

---

## 4. Log Retention Policy (Zero-Overwrite Guarantee)

Every backend and Cloudflare tunnel execution creates an **immutable, append-only timestamped file**:

- **Backend API Logs:** `logs/backend/backend-YYYYMMDD-HHMMSS-p<PID>.log`
- **Worker Logs:** `logs/backend/worker-YYYYMMDD-HHMMSS-p<PID>.log`
- **Cloudflare Tunnel Logs:** `logs/cloudflare/cloudflared-YYYYMMDD-HHMMSS-p<PID>.log`

Stable convenience symlinks:
- `logs/backend/latest.log` -> most recent API log
- `logs/backend/latest-worker.log` -> most recent worker log
- `logs/cloudflare/latest.log` -> most recent tunnel log

**Rules:**
1. No operational script ever truncates or overwrites existing log files.
2. Log files are git-ignored and never committed to version control.
3. PID files are maintained separately in `logs/backend/backend.pid` and `logs/cloudflare/cloudflared.pid`.

---

## 5. Health Verification Endpoints

The API exposes standard health and capability probe endpoints:

```bash
# Primary health probe (returns DB, Redis, Profound, Model, and Neo4j states)
curl -s http://127.0.0.1:8000/api/health

# Public tunnel health probe (using DNS-over-HTTPS or public DNS)
curl -s --doh-url https://cloudflare-dns.com/dns-query \
  https://destinations-cams-easily-comparing.trycloudflare.com/api/health

# System capabilities and subsystem breakdown
curl -s http://127.0.0.1:8000/api/system/capabilities

# Change Guard verification endpoint
curl -s -X POST http://127.0.0.1:8000/api/change-checks \
  -H "content-type: application/json" \
  -d '{"target_domain": "auth0.com", "proposed_claim": "test claim", "target_type": "domain"}'
```

---

## 6. Server Reboot Recovery Runbook

In the event of a host reboot, system services are started with:

```bash
cd "/home/923873155/Marketing Hackathon 3Oct"

# 1. Verify infrastructure containers
docker compose up -d --wait

# 2. Run database migrations to ensure head state
cd backend && .venv/bin/alembic upgrade head && cd ..

# 3. Start backend and worker under nohup
scripts/start-backend-prod.sh

# 4. Start Cloudflare Tunnel
scripts/start-cloudflare-prod.sh

# 5. Verify live status
scripts/status-backend-prod.sh
```

---

## 7. Rollback Procedure

If a deployed version causes regression:

### Backend Rollback
1. Stop backend: `scripts/stop-backend-prod.sh`
2. Check out target known-good git SHA: `git checkout <KNOWN_GOOD_SHA>`
3. Apply database migration adjustments if applicable: `cd backend && .venv/bin/alembic downgrade <REV>`
4. Restart backend: `scripts/start-backend-prod.sh`
5. Verify health: `scripts/status-backend-prod.sh`

### Frontend Rollback
1. In Vercel Dashboard, navigate to **Deployments**.
2. Select previous stable deployment and click **Promote to Production** (or run `vercel rollback <DEPLOYMENT_ID>`).

---

## 8. Environment Variable Names Reference

The following environment variables are supported in `.env` (never commit values to git):

```bash
# Server & CORS
APP_BASE_URL=https://aeo-sre.vercel.app
CORS_ALLOWED_ORIGINS=https://aeo-sre.vercel.app,http://localhost:3000
DATABASE_URL=postgresql+asyncpg://...
REDIS_URL=redis://localhost:6379/0

# Integrations
PROFOUND_API_KEY=...
ANTHROPIC_API_KEY=...
NEO4J_URI=neo4j+s://...
NEO4J_PASSWORD=...

# Frontend Runtime
NUXT_PUBLIC_API_BASE_URL=https://destinations-cams-easily-comparing.trycloudflare.com
```
