# Muse Connector — Meta review guide

## URLs (production)

| Item | URL |
|---|---|
| Product | https://aeo-sre.vercel.app |
| Connector docs | https://aeo-sre.vercel.app/docs/muse |
| Privacy | https://aeo-sre.vercel.app/privacy |
| Terms | https://aeo-sre.vercel.app/terms |
| Support | https://aeo-sre.vercel.app/support |
| OpenAPI (connector-only) | `{API_PUBLIC_URL}/muse-openapi.json` |

Set `API_PUBLIC_URL` to your stable Cloudflare tunnel hostname (not `*.trycloudflare.com`).

## Authentication

OAuth 2.0 authorization code + **PKCE (S256)**. Public client `muse-agentmatch` (configurable via `MUSE_OAUTH_CLIENT_ID`).

**Read-only preset scopes:** `matches:read discovery_gaps:read campaigns:read experiments:read`

**Optional write:** `feedback:write` for `POST /api/muse/v1/feedback` only.

## Reviewer flow

1. Create an AgentMatch account (or use the dedicated reviewer workspace — credentials issued out-of-band).
2. Settings → Connections → Connect Muse (or complete OAuth from Muse).
3. Approve **Read Only** or include feedback scope.
4. Exercise tools (see example prompts in campaign master doc).

## Example prompts

- Find a CRM in my workspace that fits SAML and EU hosting.
- Check whether AI assistants correctly understand our SAML support.
- Explain why this product matches my requirements.
- Summarize ROI for my Enterprise Discovery campaign.
- What is the status of my active SAML discovery experiment?

## Test data

Reviewer workspace uses **TEST**-labelled fixtures only (no production customer data).

## Revocation

Settings → Connections → Muse → Disconnect, or `POST /oauth/revoke`.

## Support

See https://aeo-sre.vercel.app/support
