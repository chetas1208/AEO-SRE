# Research R1: Meta Muse as an integration (not a pivot)

Accessed: 2026-10-03. Research only; no accounts created, no forms submitted, no credentials used. No code edited.
Status key: CONFIRMED (primary Meta page), CONFIRMED-2ND (secondary press/blog only), CONTRADICTED, NOT FOUND.
Quotes are short and taken from fetched page summaries; WebFetch returns model-summarised text, so re-read primary pages before relying on exact wording.

## 1. Findings table

| # | Claim (from user notes) | Status | Source / quote |
|---|---|---|---|
| 1 | A public Muse Connector Platform with a submission flow exists | CONFIRMED | https://muse.ai/platform ("Describe your product", "Submit for review", "Appear in the directory") |
| 2 | Submission needs endpoint, auth setup, credentials (OAuth client or API key), test vs prod | CONFIRMED | https://muse.ai/platform/docs ("Integration Credentials": endpoints, auth setup, OAuth client credentials or API keys) |
| 3 | Dedicated test account + sign-in instructions, test data | CONFIRMED | https://muse.ai/platform/docs ("Test Account & Demo": dedicated test account with sign-in instructions) |
| 4 | Requested scopes | PARTIAL | Docs say OAuth "with optional Read Only permission limits". Exact scope fields not visible in public docs. |
| 5 | Tool docs: read/write classification | CONFIRMED | https://muse.ai/platform/docs ("Mark each tool as Read or Write"; sensitive-write marking "coming soon to portal") |
| 6 | Tool docs: inputs/outputs, errors, side effects, rate limits | NOT FOUND | Not stated on the public guidelines page. Likely asked in the portal form. |
| 7 | Review = risk assessment, tool review, end-to-end QA | CONFIRMED | https://muse.ai/platform/docs (three stages: risk assessment, tool review, end-to-end QA) |
| 8 | Business verification also required (not in user notes) | CONFIRMED (extra) | Same page: organization details, brand assets, privacy terms, support contacts, accept developer terms |
| 9 | Data-processing disclosure required (access, retention, deletion, sharing) | CONFIRMED (extra) | Same page ("Data Processing") |
| 10 | Who can submit: open vs waitlist vs partner-only | PARTIAL | Docs/terms state no eligibility gate. Press: submissions open to anyone; onboarding "in waves over the coming weeks"; 2,000+ submissions in days (https://www.howdoiuseai.com/blog/2026-09-26-what-meta-s-muse-connectors-mean-for-anyone-buildi, https://supergok.com/muse-connector-platform/). Treat as open submit, queued review. |
| 11 | Connector format (MCP vs raw API) | CONFIRMED-2ND | howdoiuseai.com: "MCP servers" or "raw APIs" with documented endpoints. Meta's own guidelines do not specify. |
| 12 | Tool semantics: Read / Write / Sensitive Write | CONFIRMED | Read: "Retrieves information without changing data, account state, or permissions." Sensitive: "Irreversible or consequential actions" |
| 13 | Approval rules | CONFIRMED | Read/Write: "Ask on first use, then on each use unless Always allow is selected". Sensitive Write: every use, Allow once or Deny only. |
| 14 | Data handling limits | CONFIRMED | "Use the minimum data required for each tool"; prohibited: selling data, unrelated advertising, model training. Encrypt in transit; notify Meta within 48h of incidents (vendor-incident@meta.com). |
| 15 | Platform terms | CONFIRMED | https://muse.ai/platform/terms (independent controllers; Meta "may decline, suspend, disable, or remove any Connector at any time") |
| 16 | Connector receives authorized task context / user identity | NOT FOUND | No public page says what Muse passes (task text, user id, tokens). With OAuth the connector sees whichever account the user links; that is inferred, not documented. |
| 17 | How consent is shown to the user | CONFIRMED (partial) | Per-tool approval prompts (row 13); "People choose which apps Muse connects to" (https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/). |
| 18 | Public hosting: internet reachable, TLS, domains, latency timeouts | NOT FOUND | Only "Encrypt data in transit" is stated. Reachability and timeouts unspecified. Muse runs in Meta cloud, so a public HTTPS endpoint is the safe assumption. |
| 19 | Rate limits for connectors | NOT FOUND | Terms only forbid circumventing rate limits. |
| 20 | Model API base URL `https://api.meta.ai/v1` | CONFIRMED | https://dev.meta.ai/docs/ ("https://api.meta.ai/v1", bearer auth) |
| 21 | Model `muse-spark-1.3` | CONFIRMED | https://dev.meta.ai/docs/models (1.3 "recommended"); also 1.2, 1.1, and `-contributor` variants. Older blog page still says 1.1. |
| 22 | Responses + Messages + Chat protocols | CONFIRMED | https://dev.meta.ai/docs/ (Responses API, Chat Completions API, Messages API) |
| 23 | Auth header | PARTIAL | "bearer token authentication"; env var named MODEL_API_KEY in Meta docs. |
| 24 | Pricing | CONFIRMED-2ND | 1.1 quick reference: "$1.25 input / $4.25 output per million tokens". 1.3 pricing and rate limits not captured; check dev.meta.ai/docs. Contributor tier is cheaper but Meta may train on prompts. |
| 25 | Structured output and tool calling | CONFIRMED | dev.meta.ai/docs ("Structured JSON output matching schemas", parallel tool calls). Reasoning model with `reasoning_effort`; reasoning tokens billed as output. |
| 26 | Standard tier data not used for training | CONFIRMED | https://dev.meta.ai/docs/models ("Standard... data not used for training"; Contributor: Meta may use prompts/completions) |
| 27 | Muse personal data separated from Meta ad systems | CONFIRMED (scope-limited) | https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/ ("doesn't share a person's conversations or the data in their VM with Meta's ad systems") . This covers Muse users, not the Model API or connector terms. Training is opt-out. |
| 28 | Hackathon / program path | PARTIAL | https://dev.meta.ai/events/global-hackathon: 10-day virtual, $1M prizes, $150 API credits each, status "Applications opening soon". No dates, eligibility or rules published; connectors not mentioned. |
| 29 | Model API self-serve signup | CONFIRMED | https://dev.meta.ai/products/meta-model-api ("Generate a one-click API key"); status "Public preview". Earlier press said US-only; current page says "expanded global access". |

## 2. Still unknown
- What Muse sends to a connector (user identity, prompt text, session id) and whether per-call context is documented anywhere outside the portal.
- Required transport (MCP vs plain HTTPS), timeouts, payload limits, rate limits, retry behaviour.
- Exact scope/permission fields in the portal form and whether a Read-only OAuth scope maps to our token.
- Whether a B2B/enterprise-ops tool (change gating) is acceptable in a consumer Muse directory, since Muse is a consumer personal agent. This is a product-fit risk, not a documented rule.
- Time to review (waves); a hackathon demo cannot depend on approval.
- Whether unapproved connectors can be tried by the submitter (a dev or test mode). Not documented.
- Model API: 1.3 pricing, rate limits, whether `/v1/responses` honours `json_schema` strictly, regional eligibility for the user.
- Hackathon dates and rules.

## 3. Minimum validation the user must do themselves
1. Open https://muse.ai/platform/docs and read "Core requirements", "Tool permissions" and "Submit through the Muse Connector Platform". Check for transport (MCP vs HTTPS), timeouts and the context passed to tools.
2. Open https://muse.ai/platform, start the form without submitting, and note the real required fields (stop before final submit if not ready).
3. Sign up at https://dev.meta.ai (one-click API key; do not paste it into chat). Put it in local `.env` as MODEL_API_KEY with MODEL_API_PROTOCOL=openai_responses, MODEL_BASE_URL=https://api.meta.ai/v1, MODEL_NAME=muse-spark-1.3, then run `make model-smoke` (exit 0 = READY). This is the single fastest real validation.
4. Register interest for the hackathon at https://dev.meta.ai/events/global-hackathon (email form only, no dates yet).

## 4. Integration sketch (no pivot; reuse existing API)

### Role B: Muse Spark as ModelGateway provider (small, do first)
- Existing gateway (docs/notes/handoff-b3.md; `backend/app/connectors/llm/adapters.py`, `client.py`) supports `openai_chat`, `openai_responses`, `anthropic_messages` with configurable base URL, key and name. Meta documents all three protocols with bearer auth, so env-only config should work:
  `MODEL_PROVIDER=meta`, `MODEL_API_PROTOCOL=openai_responses` (or `openai_chat`), `MODEL_BASE_URL=https://api.meta.ai/v1`, `MODEL_NAME=muse-spark-1.3`, `MODEL_API_KEY=<local only>`.
- Verdict: probably env-only, NOT live-verified (B3 states no provider was live-tested).
- Risks and gaps:
  - Reasoning model: reasoning tokens count against `max_output_tokens`; the gateway's task defaults may truncate JSON. Raise per-task limits via `MODEL_TASK_OVERRIDES` and watch for empty `text`. Also `temperature` may be rejected for reasoning models; the adapter adapts once on a 400.
  - Meta advises developer messages over system messages. Our adapters send `system`; behaviour untested.
  - Use the Standard tier, not `-contributor` (training on prompts). Never send customer data to the contributor tier.
  - Gateway deliberately uses prompt-enforced JSON, not native json_schema, so the Meta structured-output feature is unused, which is fine.
  - Pricing and rate limits unverified for 1.3; set `MODEL_MAX_CONCURRENCY` low for the demo.

### Role A: Muse connector exposing Change Guard (larger, gated by Meta review)
Candidate tools, each mapped to existing routes (see docs/CHANGE_GUARD_INTEGRATION.md; `backend/app/api/routes/change_checks.py`, `canonical_claims.py`, `interventions.py`):

| Tool | Class | Backs onto | Notes |
|---|---|---|---|
| check_change | Read if the check is a pure evaluation. If `POST /api/change-checks` persists a check record, it is a Write (non-sensitive). Classify honestly; mislabelling fails tool review. | `POST /api/change-checks` -> ALLOW/MERGE/DELAY/REQUIRE_REVIEW/BLOCK | Decision is advisory; Muse must not be able to approve or apply a change. |
| verify_claim | Read | canonical-claims lookup/compare (confirm the exact route) | Returns canonical value, source, valid_until. |
| list_discovery_gaps | Read | existing discovery/gap endpoints (confirm route; not checked in this research) | Must be scoped per org. |
| record_feedback | Write (non-sensitive) | existing feedback route if any (not verified) | Prompts each time unless user picks Always allow. |

Do not expose approval, apply-intervention or delete endpoints; those would be Sensitive Writes and add review burden.

Architecture: thin adapter service (MCP server or HTTPS wrapper, per portal requirement) in front of the existing FastAPI; it holds the org-scoped bearer token, maps Muse caller to an org, and calls the existing API. Do not change Change Guard internals.

Gaps and risks:
- Auth: Meta offers OAuth (optional read-only limits) or API keys. We currently have a static bearer token model (per integration doc); per-user OAuth with org mapping does not exist. For a demo an API-key connector scoped to one demo org is the cheapest option.
- Identity: not documented what Muse passes, so do not rely on caller identity for authorization; keep org binding in our token.
- Hosting: needs a public HTTPS endpoint (same requirement as the Profound Call API path); timeouts unknown, so keep check_change fast and deterministic (no LLM in the request path).
- Data: send only minimum fields; a proposed change may contain customer content. Disclose retention/deletion in the submission; do not train or sell.
- Prompt injection: Muse passes user/model-authored text into tools; our existing untrusted-data handling must treat tool input as data.
- Business verification, privacy policy, support contact and a dedicated test account with seeded data are needed; review is queued in waves, so approval is not demo-critical. Demo path: show the connector contract and call the endpoint directly, or via a Muse Code / API client.
- Product fit: Muse is consumer-oriented; an SRE gating tool is an odd fit and may be declined (Meta may remove any connector for any reason).
- Meta partners with Stripe for payments; irrelevant to us (no money movement).

### Suggested order
1. Role B env smoke test (hours, no review dependency).
2. Draft the connector submission pack offline (tool docs, classification, data processing, test account) so it is ready when the portal fields are confirmed.
3. Build the adapter only after step 1 of section 3 clarifies transport and context.

## 5. Sources
- https://muse.ai/platform
- https://muse.ai/platform/docs
- https://muse.ai/platform/terms
- https://about.fb.com/news/2026/09/introducing-muse-personal-ai-agent/
- https://dev.meta.ai/docs/ , https://dev.meta.ai/docs/models , https://dev.meta.ai/products/meta-model-api
- https://dev.meta.ai/resources/blog/build-with-muse-spark/ (1.1-era quick reference)
- https://dev.meta.ai/events/global-hackathon
- Secondary: https://www.howdoiuseai.com/blog/2026-09-26-what-meta-s-muse-connectors-mean-for-anyone-buildi , https://supergok.com/muse-connector-platform/
