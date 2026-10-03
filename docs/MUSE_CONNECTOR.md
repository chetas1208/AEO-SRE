# Muse connector (AgentMatch as OAuth provider)

## Relationship

```text
Muse user → Connect AgentMatch → OAuth → scoped token → /muse/tools/*
```

AgentMatch is the **resource server**. Muse is the client.

**Current (V0):** single org bearer key (`MUSE_CONNECTOR_API_KEY`). Suitable for hackathon demo only.

**Target (V1):** OAuth 2.0 authorization code + PKCE, per-user tokens bound to one organization.

## Scopes (minimal)

| Scope | Access |
|---|---|
| `matches:read` | `find_matches`, `explain_match` |
| `discovery_gaps:read` | `check_ai_perception`, list gaps |
| `campaigns:read` | `get_campaign_summary` |
| `experiments:read` | `get_experiment_status` |
| `feedback:write` | `record_feedback` (write scope) |

Offer a **Read only** consent preset (matches + gaps + campaigns + experiments, no `feedback:write`).

No publish/spend/send scopes in V1.

## Endpoints (planned)

```text
GET  /.well-known/oauth-authorization-server
GET  /oauth/authorize
POST /oauth/token
POST /oauth/revoke
```

Tool base (existing): `/muse/tools/*` (see `app/integrations/muse/`).

## Data handling (Meta connector policy)

- Minimum necessary data per request.
- **Do not** use connector data for unrelated ads, profiling, sensitive inference, or **training** Laya/bandit/LLM/embeddings.
- Authorized Muse context may inform **the active request only**.
- User can disconnect in **Settings → Connected Apps → Muse** (revoke + delete connection metadata).

## Test users

Document Muse test-user credentials in operator runbook (never commit secrets).

## Revocation

Disconnect must invalidate refresh tokens immediately; access tokens rejected on next call.
