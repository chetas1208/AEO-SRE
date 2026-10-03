# Handoff A15 (LLM connector)

Status: done. `cd backend && .venv/bin/python -m pytest tests/unit/test_llm_client.py` -> 26 passed; ruff clean. No git operations.
Live smoke call: NOT exercised (root `.env` MODEL_API_KEY is unset). All tests use respx; no real model is ever called.
Nothing copied from `references/*` (no third-party notes needed).

## Files
`backend/app/connectors/llm/{__init__,client,errors,prompts,status}.py`, `backend/tests/unit/test_llm_client.py`, `docs/notes/requests-a15.md`.

## Public interface (`from app.connectors.llm import ...`)
- `get_llm_client(settings=None) -> ModelClient | None` (None when MODEL_API_KEY unset or protocol not anthropic|openai).
  `get_sync_llm_client()` -> `SyncModelClient | None`. `get_llm_or_unavailable()` -> client or `UnavailableLLM` (falsy; calls raise `LLMUnavailable`).
- Settings used: MODEL_API_KEY, MODEL_BASE_URL, MODEL_NAME, MODEL_API_PROTOCOL. Base URL may be `https://host`, `https://host/v1`, or the
  full endpoint; `/v1/messages` or `/v1/chat/completions` is derived.
- `ModelClient` (async, httpx, no SDK): `await complete_json(*, system, user=None, prompt=None, schema=None, untrusted=None, max_tokens=None,
  temperature=None, purpose="json")` and `complete_text(...) -> LLMResult`. Accepts either `user=` (A10) or `prompt=` (A3). `schema` = pydantic class
  (returns validated instance) or JSON-schema dict (returns dict after shallow required/type check). `untrusted={"label": text}` appends
  delimited `<untrusted_data>` blocks. `.name` = `"<protocol>:<model>"` (used by rca for `produced_by`).
- `.sync()` / `SyncModelClient`: blocking `complete_json(system, prompt|user, schema)` using `httpx.Client`; required for A3's sync
  `run_rca`/`generate_hypotheses`, which discards async clients.
- Behaviour: JSON-only system prompt + schema embedded + standing untrusted-data rules; fenced/prose-wrapped JSON tolerated; one repair retry
  (assistant reply echoed + error summary) then `LLMInvalidOutput`; retries (default 2, exponential backoff + jitter, honours Retry-After <= 30s) on
  408/425/429/5xx/529, timeouts and connect errors; 4xx auth/config errors fail fast with `LLMUnavailable(status_code=...)`; OpenAI
  `max_tokens` -> `max_completion_tokens` auto-fallback; default per-request timeout 60s; no temperature sent unless asked.
- Errors: `LLMError` > `LLMUnavailable(status_code, retryable)`, `LLMInvalidOutput(raw_preview)`. Messages never include key; key is scrubbed from provider messages.
- Logging (structlog `llm.call`, `llm.invalid_output`): protocol, model, purpose, latency, attempts, token counts, char counts. Never prompt text, outputs or keys.
- `prompts.untrusted_block(label, text)`, `build_user_prompt(task, untrusted)`: neutralise control chars and any attempt to forge the delimiter.
- `llm_capability() -> LLMCapability` (dataclass; `.state: CapabilityState`, `last_success`, `last_error`, `last_error_at`, `detail`, `meta`
  incl. call counts/token totals; `.as_dict()` fits `schemas.system.Capability`). Rules: no key/bad protocol -> `unavailable`; configured, no calls
  -> `healthy` ("no model call made yet"); last call failed -> `degraded` (earlier success or invalid output) or `unavailable` (never succeeded);
  next success -> `healthy`.

## Adapter check
- A3 rca: `arun_rca(..., get_llm_client())` works (async); `run_rca(..., get_sync_llm_client())` works (sync). Verified in tests, including
  degradation to rules when the model returns 500. rca's own `<evidence>` tags + system instruction stay as A3 wrote them; they are not routed through
  `<untrusted_data>` (our client still appends its standing untrusted-data rules to the system prompt).
- A10 patching: `ModelClient` satisfies `interventions.llm.LLMClient` (runtime isinstance verified). Wiring `default_llm_client` is requested in `requests-a15.md`.
- Pipeline callers must pick sync vs async client as above.

## Known gaps
- Capability state is in-process only (API and worker processes each track their own); no persistence, no active probe (A12's `?probe=true` remains).
- No streaming, tool-use or provider-native JSON modes (deliberately prompt-only for compatibility with arbitrary Anthropic/OpenAI-compatible gateways).
- Anthropic auth is `x-api-key` only; gateways needing Bearer on the anthropic protocol are unsupported (use `openai` protocol).
- Never run against a real model here; first real-model behaviour (JSON adherence, max_tokens truncation of long outputs) is untested.
- Shallow dict-schema validation only; callers validate semantics themselves (rca, patching do).
