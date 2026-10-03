# Handoff B3 (provider-neutral model runtime)

Status: done. No live call was made (MODEL_API_KEY unset); every adapter is contract-tested with respx only.
`cd backend && .venv/bin/python -m pytest tests/unit/test_model_gateway.py tests/unit/test_llm_client.py` -> 51 passed. No git operations.

## What exists (all under `backend/app/connectors/llm/`)
- `gateway.py`: `ModelGateway` Protocol (`generate_structured(request, schema)`, `generate_text(request)`, `health()`), `ModelRequest`
  (purpose, system, messages/input, untrusted, temperature, max_output_tokens, timeout_s, response_schema, prompt_version, metadata),
  `ModelResponse` (text, parsed, meta), `ModelCallMeta`, `ModelCapabilities` (structured_output, json_schema, tool_calling, streaming,
  max_context), `ModelHealthState` (NOT_CONFIGURED | READY | AUTH_FAILED | DEGRADED), `ModelPurpose`, `TASK_DEFAULTS`, `UnsupportedModelProtocol`.
- `adapters.py`: `OpenAICompatibleAdapter` (`openai_chat` -> /chat/completions, `openai_responses` -> /responses) and `AnthropicAdapter`
  (`anthropic_messages`, `/v1/messages`; also sends Bearer on non-anthropic.com hosts). Pure wire shape; no NVIDIA-specific adapter.
- `client.py`: `ModelClient` = the concrete gateway (httpx), `.sync()` twin, `create_model_gateway(settings)`, `get_llm_client` (None when not
  configured/unsupported; legacy callers), `UnavailableLLM` (NOT_CONFIGURED null gateway). Legacy `complete_json/complete_text` still work.
- `registry.py`: prompt registry (`hypothesis_generator_v3`, `intervention_drafter_v2`). Only prompts with a consumer exist; classifier,
  counterevidence, summarizer and extraction purposes exist in the enum/defaults but have no prompt until a caller needs one.
- `status.py`: counters (calls/failures/latency per purpose, tokens), ring buffer of `ModelCallMeta`, `current_health()`, `llm_capability()`.

## Config (`app/core/config.py`, `.env.example`)
MODEL_PROVIDER (label), MODEL_API_PROTOCOL (`openai_chat|openai_responses|anthropic_messages`; aliases `anthropic`, `openai` still work),
MODEL_BASE_URL (empty -> official default per protocol), MODEL_API_KEY, MODEL_NAME; optional MODEL_MAX_CONCURRENCY (4), MODEL_MAX_RETRIES (2),
MODEL_CACHE_ENABLED, MODEL_ALLOW_NO_KEY (keyless self-hosted), MODEL_TASK_OVERRIDES (JSON per purpose). Per-task temperature/max tokens/
timeout live in `gateway.TASK_DEFAULTS` (state-affecting tasks <= 0.2).

## Behaviour
- Structured calls: prompt-enforced JSON + Pydantic validation -> one repair attempt (echoing only the validation error) -> `LLMInvalidOutput`.
  Never retried beyond that. Transport retries (bounded, backoff, Retry-After) only on timeout/connect/408/425/429/5xx/529.
  401/403 -> `LLMAuthError`, wrong model -> `LLMModelNotFound`; neither retried. A 400 about `max_tokens`/`temperature` adapts once.
- Concurrency semaphore (per event loop for async, thread semaphore for sync). Cache: in-memory LRU, only EXTRACTION/CLASSIFICATION at temp 0.
- Safety: untrusted content only via delimited blocks (`<untrusted_data>`; rca keeps `<evidence id=..>` but both tags are now defused in
  untrusted text); system prompt carries standing rules; evidence cited by immutable ids, unknown ids discarded (existing behaviour, tested);
  model-mentioned URLs not among retrieved evidence URLs are replaced by `[unverified url removed]` with a warning; reasoning/thinking blocks are
  never read or stored; drafter output `action` must equal the policy-selected action else the draft is rejected and the template is used.
- Metadata per call: structlog `model.call` (provider, model, protocol, purpose, prompt_version, latency, attempts, tokens, ok, error_kind,
  cached; never prompt/output/key), `incidents.context["llm"]["calls"]`, and `hypotheses.produced_by = llm:<protocol>:<model>@<prompt_version>`.
  Optional `model_calls` table requested in `schema-requests.md` (not blocking).
- Health feeds `services/capabilities.check_llm` through `llm_capability()` (meta.health = NOT_CONFIGURED|READY|AUTH_FAILED|DEGRADED).
  Provider down -> rca returns rules-only hypotheses with a warning (investigation shows llm unavailable), rest of pipeline continues.

## Call-site migration
`investigation/rca.py` (`_call_llm`: `generate_structured` when the client is a gateway, legacy `complete_json` for fakes) and
`interventions/patching.py` (gateway path with facts as untrusted block; legacy path kept). `interventions/llm.py`: removed the duplicate
`AnthropicLLMClient` (raw HTTP) and its test; `LLMUnavailable` re-exported from `connectors.llm.errors`; `PatchDraft.action` added.
`backend/app/core/llm.py` did not exist. Grep: no `openai.OpenAI(`/`anthropic.Anthropic(`/provider URLs outside `connectors/llm` (config holds default base URLs).

## Tests
`tests/unit/test_model_gateway.py`: the same hypothesis + drafting calls run against fake openai_chat, openai_responses and anthropic_messages
servers; malformed JSON/repair, unknown ids, invented URL, injection, 429/5xx/timeout, wrong model, invalid key, key never in logs, caching,
semaphore, metadata, defaults. `tests/unit/test_llm_client.py` (A15) updated for alias/health changes.

## Gaps
- Not live-tested against any provider; json_schema/native structured output and tool calling are deliberately not used (capability flags say so).
- Counters are per process. `make model-smoke` is the cutover check: exit 0 READY, 1 degraded, 2 not configured, 3 auth failed.
- Nothing calls INTENT classification / counterevidence / summary prompts yet (no consumer in the codebase).

## README model section (for B5)
> **Model (optional).** AEO SRE works without a model (rules-only; investigations show DEGRADED reasoning). To enable model-assisted
> hypotheses and content drafts set `MODEL_API_PROTOCOL` (`openai_chat`, `openai_responses` or `anthropic_messages`), `MODEL_BASE_URL`,
> `MODEL_API_KEY`, `MODEL_NAME` (and optionally `MODEL_PROVIDER` as a label), then run `make model-smoke`. OpenAI-compatible servers
> (OpenAI, NVIDIA NIM, vLLM) use the `openai_*` protocols with their base URL; Anthropic uses `anthropic_messages`. The model only
> proposes; evidence ids are checked, web content is treated as data, policy picks the action, and humans approve. Adapter-tested (mocked
> HTTP contract tests): OpenAI-compatible chat, OpenAI-compatible responses, Anthropic Messages. Not live-verified until a key is supplied.
