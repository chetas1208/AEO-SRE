# Requests from A15 (LLM connector)

1. A10 (`backend/app/interventions/llm.py`, `default_llm_client`): currently returns None unless protocol == anthropic and uses a
   forced tool call. Suggest replacing the body with
   `from app.connectors.llm import get_llm_client; return get_llm_client()`.
   `ModelClient.complete_json(*, system, user, schema)` already satisfies the existing `LLMClient` Protocol (verified with
   `isinstance` in tests), supports both protocols, retries and repair. Existing `AnthropicLLMClient` can stay for its tests.
2. A12 (`backend/app/services/capabilities.py::check_llm`): try-import
   `from app.connectors.llm import llm_capability` and build `Capability(**llm_capability().as_dict())` (fields: key, label, state,
   detail, last_success, last_error, last_error_at, meta). State is per-process (see handoff). Keep the `?probe=true` reachability
   check as is if desired.
3. A14 (`backend/app/core/llm.py`, unowned, only self-referenced): duplicate minimal client with a third protocol shape
   (`prompt=`, async only). Nothing imports it; suggest deleting it or turning it into a re-export of `app.connectors.llm`.
4. A14 / pipeline: call RCA via `arun_rca(incident, evidence, get_llm_client())` (async path) or
   `run_rca(incident, evidence, get_sync_llm_client())` (sync path). Passing the async `ModelClient` to the sync `run_rca`
   gets the LLM output discarded with a warning (A3 design); the sync facade avoids that.
