# Change Guard: Profound Agent integration

Status: **designed against Profound's public docs, not exercised with a real Profound Agent.** Nothing in this repository has
received traffic from a live Profound Agent; demo data comes from `scripts/simulate_agent_change.py` and is labelled
`SIMULATED` everywhere.

Flow: Profound Agent -> Call API node -> `POST /api/change-checks` (Profound Lift Change Guard) -> one decision -> the Agent
branches on `decision`.

Change Guard does not replace any review step a Profound Agent already has (for example its Human Review node). It targets a
different gap: **cross-run coordination**, i.e. this change versus other active changes, versus an organisation's canonical
claims, and versus experiments that are still being measured.

## What Profound's public docs say (verified 2026-10-03)

| Topic | What the docs state | Source |
|---|---|---|
| Call API node | Connects an Agent to an external API endpoint. Method GET or POST; URL (variables from inputs or earlier steps can be inserted); Output Label (becomes the variable name downstream nodes reference); for POST a JSON request body and Content Type (None or Application/Json). Authentication under Advanced settings: None, Basic, Bearer, or API Key (custom header name + value). "Returns the full response body from the API as text", usable in LLM steps, logic nodes or downstream API calls. The page does not document timeouts, response-size limits or custom headers beyond Content Type. | https://help.tryprofound.com/articles/2527960262-call-api |
| Run an agent | `POST /v1/agents/{agent_id}/runs`, body `inputs` keyed by the Agent's input schema; 202 with run id and status. The Agent must be published first; unpublished drafts cannot be run. Auth: `X-API-Key` or bearer JWT. | https://docs.tryprofound.com/api-reference/agents/run-an-agent.md |
| Get an agent run | `GET /v1/agents/{agent_id}/runs/{run_id}`. Status: queued, running, succeeded, failed, cancelled, skipped, unknown. Fields include `outputs`, `outputs_expanded`, `steps` (execution trace). Query `verbose=true` "includes each step's raw `outputs` payload in the execution trace" (default false). | https://docs.tryprofound.com/api-reference/agents/get-an-agent-run.md |
| Human Review node | Adds a review step inside an Agent; a person approves, rejects or edits an output before the run continues. Works only with text/string values; not compatible with the Iteration node. | https://help.tryprofound.com/articles/2362724937-human-review |

Consequences for this integration:

- There is no documented "pending change" feed from Profound, so Agents **push** their intended change to us. That is the only
  integration path assumed.
- The Call API node returns the response as **text**. Whether a downstream logic node can read a JSON field directly is not
  documented; the safe pattern below passes the text to a step that extracts `decision`.
- Custom headers are not documented beyond Content Type, so authenticate with the node's **Bearer** option (sends the
  `Authorization: Bearer <token>` header; the Bearer scheme is what the docs name). Do not rely on a custom header.
- To audit what a run actually sent and received, fetch the run with `verbose=true` and read the Call API step's `outputs`.

## Unsolved external requirement: reachability

Profound Agents run in Profound's cloud. `POST /api/change-checks` must therefore be reachable **from the public internet**.
Our API currently runs locally (`http://localhost:8000`). A tunnel or a deployed instance is needed and **has not been set up**;
this feature does not solve it and this document does not name a public URL. Until one exists the only way to exercise the
endpoint is the simulator. When one exists, serve it over HTTPS only, keep `CHANGE_GUARD_TOKEN` set, and consider an
additional network-level allow-list. If `CHANGE_GUARD_TOKEN` is unset the endpoint answers `503 CHANGE_GUARD_NOT_CONFIGURED`.

## Call API node configuration

Replace `<PUBLIC_BASE_URL>` with the HTTPS origin you expose (not provided here).

| Field | Value |
|---|---|
| Method | `POST` |
| URL | `<PUBLIC_BASE_URL>/api/change-checks` |
| Content Type | `Application/Json` |
| Authentication (Advanced) | `Bearer`, token = the value of `CHANGE_GUARD_TOKEN` (store it as a secret in Profound, never in a prompt or output) |
| Output Label | `change_check` |
| Request Body | the template below |

Insert the `<...>` placeholders with the editor's variable picker (the docs describe inserting variables from inputs or earlier
steps with `/`; confirm the exact token syntax in the node editor, it is not reproduced here).

```json
{
  "org_domain": "<input: organisation domain>",
  "agent": { "id": "<agent id>", "name": "<agent name>" },
  "profound_run_id": "<run id, if the Agent can reference it>",
  "source_mode": "LIVE",
  "target_url": "<step: page this Agent intends to change>",
  "action_type": "update_existing_page",
  "proposed_claims": ["<step: claim 1>", "<step: claim 2>"],
  "proposed_text": "<step: proposed copy, optional>",
  "reason": "<step: why>",
  "expected_kpi": "citation_share",
  "risk": "low",
  "reversible": true,
  "idempotency_key": "<stable per run and change, e.g. agent id + run id>"
}
```

Optional: `prompt_cluster_ids` (uuids) and `prompts` list what prompts the change addresses, which enables prompt-cluster overlap; `recheck: true` forces a fresh evaluation instead of replaying a stored decision.

Field mapping to the `ChangeSet`:

| Body field | Meaning | Notes |
|---|---|---|
| `org_id` or `org_domain` | which organisation's canonical truth and experiments apply | one is required |
| `agent.id`, `agent.name` | who proposes | shown in the Change checks panel |
| `profound_run_id` | optional run reference | used in the default idempotency key |
| `source_mode` | `LIVE` or `SIMULATED` | a real Agent sends `LIVE`; the simulator sends `SIMULATED`; never mislabel |
| `target_url` | page the change touches | normalised server-side (case, trailing slash, tracking parameters, fragment, scheme, `www`, percent-encoding) |
| `action_type` | one of `observe`, `update_existing_page`, `create_faq`, `create_canonical_page`, `create_comparison_content`, `publisher_outreach`, `structured_data` | |
| `proposed_claims` | list of strings, the factual claims the change will assert | what canonical-truth checks run on |
| `proposed_text`, `proposed_diff` | optional | hashed into the digest |
| `reason`, `expected_kpi`, `risk`, `reversible` | context | |
| `idempotency_key` | optional | see below |

JSON in the body must be valid: if a claim can contain quotes or newlines, have an earlier step output JSON-escaped strings.

## Response contract

`201`/`200` with JSON. Decision is one of `ALLOW | MERGE | DELAY | REQUIRE_REVIEW | BLOCK` (precedence BLOCK > DELAY >
REQUIRE_REVIEW > MERGE > ALLOW). Key fields:

```json
{
  "id": "<change check id>",
  "decision": "DELAY",
  "digest": "<sha256 of the canonical proposal>",
  "eligible_after": "2026-10-04T20:29:43Z",
  "findings": [
    { "type": "experiment_contamination", "severity": "high", "reason": "...",
      "references": { "experiment_code": "EXP-0001" }, "eligible_after": "2026-10-04T20:29:43Z" }
  ],
  "semantic_check": "ok | degraded | skipped_no_canonical_truth",
  "source_mode": "LIVE",
  "replayed": false
}
```

`eligible_after` for a DELAY comes from the verification window service: the window start while the measurement cannot begin yet, the window end while it is open and unmeasured. If verification is overdue no time can be given and none is invented. Authoritative shape: `/openapi.json`. Every finding is returned, not only the one that decided. `semantic_check: degraded` means
the model/ranker part did not run and only deterministic rules decided; `skipped_no_canonical_truth` means the organisation has no
canonical claims yet, so that check did not run. Neither is a pass.

## How the Agent should branch on `decision`

The Call API output arrives as text. Pass `change_check` to a step that reads the `decision` value, then branch:

| decision | Agent behaviour |
|---|---|
| `ALLOW` | continue to the publish/CMS step |
| `MERGE` | do not publish separately; take the merged proposal from the findings, or stop and let the other change proceed |
| `DELAY` | do not publish. Stop or wait until `eligible_after`, then **re-submit** (a new check yields a fresh decision and digest; a DELAY never turns into approval by itself) |
| `REQUIRE_REVIEW` | route to a human (for example the Human Review node, which handles text values) with the findings text |
| `BLOCK` | do not publish; surface the findings; a human must resolve the conflict or fix the canonical claim |

Fail closed: if the Call API node errors, times out, or the `decision` cannot be read, treat it as **not ALLOW**.

## Idempotency

Send a stable `idempotency_key` per intended change (default if omitted: agent id + run id + digest). Re-sending the same
key with the same proposal returns the stored decision with `"replayed": true`; the same key with a different proposal returns
`409`. Use this so Agent retries do not create duplicate change records. Re-check after a DELAY with a **new** key (the
proposal context changed).

## Errors

Error body: `{"error": {"code", "type", "message", "details", "request_id"}}`.

| HTTP | code | Meaning | Agent action |
|---|---|---|---|
| 401/403 | auth error | missing/invalid bearer token | fix configuration; treat as not ALLOW |
| 503 | `CHANGE_GUARD_NOT_CONFIGURED` | server has no `CHANGE_GUARD_TOKEN` | not ALLOW; operator fix |
| 409 | idempotency conflict | key reused with a different proposal | use a new key |
| 413 | `PAYLOAD_TOO_LARGE` | body over 1 MiB | send smaller text/diff |
| 422 | validation | malformed or unknown fields | fix the body template |
| 5xx / timeout | | server or network trouble | not ALLOW; retry with the same key |

## Approval binding (inside Profound Lift)

Approvals bind to the proposal digest. If a proposal is edited after approval, activation or manual execution recording returns
`409 APPROVAL_DIGEST_MISMATCH` and the edit needs a new approval. This applies to Profound Lift's own interventions and to external
ChangeSets.

## Trying it without Profound

```bash
export CHANGE_GUARD_TOKEN=...   # same value the API was started with
python scripts/simulate_agent_change.py --scenario all --seed-canonical
```

All requests are `SIMULATED`. The script never modifies or verifies an experiment.
