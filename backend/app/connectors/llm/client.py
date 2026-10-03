"""Provider-agnostic LLM client over raw httpx (no vendor SDK).

Protocols: Anthropic Messages (`/v1/messages`) and OpenAI-compatible chat completions (`/chat/completions`).
One client class serves every caller in the repo:

* async  `await client.complete_json(system=, user=|prompt=, schema=)`  -> A10 patch generator, A3 `arun_rca`
* sync   `client.sync().complete_json(system=, prompt=|user=, schema=)`  -> A3 `run_rca` / `generate_hypotheses`
  (A3's sync path discards awaitables, so the sync facade is a real blocking client using `httpx.Client`).

`schema` may be a pydantic model class (returns a validated instance) or a JSON-schema dict (returns a dict after a
shallow structural check). Invalid JSON gets exactly one repair retry, then `LLMInvalidOutput`.
"""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import random
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Generator, Mapping
from dataclasses import dataclass
from typing import Any, TypeVar, overload

import httpx
import structlog
from pydantic import BaseModel, ValidationError

from app.connectors.llm import status
from app.connectors.llm.adapters import adapter_for
from app.connectors.llm.errors import LLMAuthError, LLMInvalidOutput, LLMModelNotFound, LLMUnavailable
from app.connectors.llm.gateway import (
    ModelCallMeta,
    ModelCapabilities,
    ModelHealth,
    ModelHealthState,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelTier,
    UnsupportedModelProtocol,
    estimate_cost_usd,
    task_defaults,
)
from app.connectors.llm.prompts import build_system_prompt, build_user_prompt
from app.core.config import Settings, get_settings, normalize_model_protocol

log = structlog.get_logger("llm")

T = TypeVar("T", bound=BaseModel)
Schema = type[BaseModel] | Mapping[str, Any] | None

RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504, 529})
MAX_RETRY_AFTER = 30.0


@dataclass(frozen=True)
class LLMResult:
    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    stop_reason: str | None = None
    latency_ms: int = 0
    attempts: int = 1


# ---------------------------------------------------------------------------------------------- pure helpers


def endpoint_for(protocol: str, base_url: str) -> str:
    return adapter_for(normalize_model_protocol(protocol)).endpoint(base_url)


def _scrub(text: str, secret: str) -> str:
    text = text.replace(secret, "***") if secret else text
    return re.sub(r"(sk-|key-)[A-Za-z0-9_\-]{8,}", "***", text)


def _error_message(resp: httpx.Response, secret: str) -> str:
    msg = ""
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        msg = (err.get("message") if isinstance(err, dict) else err) or ""
    except ValueError:
        msg = resp.text
    return _scrub(str(msg).strip().replace("\n", " ")[:200], secret)


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("retry-after")
    try:
        return min(max(float(raw), 0.0), MAX_RETRY_AFTER) if raw else None
    except ValueError:
        return None


def extract_json_object(text: str) -> Any:
    """Parse a JSON value out of model text: tolerates fences and leading/trailing prose. Raises ValueError."""
    s = text.strip()
    fence = re.match(r"^```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$", s, re.DOTALL)
    if fence:
        s = fence.group(1).strip()
    try:
        return json.loads(s)
    except ValueError:
        pass
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(s[start : i + 1])
                    except ValueError:
                        break
        start = s.find("{", start + 1)
    raise ValueError("no valid JSON object found in model output")


_JSON_TYPES: dict[str, tuple[type, ...]] = {
    "string": (str,), "boolean": (bool,), "array": (list,), "object": (dict,), "null": (type(None),),
    "integer": (int,), "number": (int, float),
}


def check_json_schema(obj: Any, schema: Mapping[str, Any]) -> None:
    """Shallow structural check (type, required keys, property types). Raises ValueError. Full validation of
    semantics stays with the caller (pydantic model or its own validator)."""
    if not isinstance(obj, dict):
        raise TypeError("top-level value must be a JSON object")
    missing = [k for k in schema.get("required", []) if k not in obj]
    if missing:
        raise ValueError(f"missing required keys: {', '.join(missing)}")
    for key, prop in (schema.get("properties") or {}).items():
        t = prop.get("type") if isinstance(prop, Mapping) else None
        if key not in obj or not isinstance(t, str) or t not in _JSON_TYPES:
            continue
        v = obj[key]
        bad = (isinstance(v, bool) and t in ("integer", "number")) or not isinstance(v, _JSON_TYPES[t])
        if bad and not (v is None and "null" in str(prop.get("anyOf", ""))):
            raise ValueError(f"key '{key}' must be {t}")


def _schema_info(schema: Schema) -> tuple[str | None, type[BaseModel] | None, Mapping[str, Any] | None]:
    if schema is None:
        return None, None, None
    if isinstance(schema, type) and issubclass(schema, BaseModel):
        js = schema.model_json_schema()
        return json.dumps(js, separators=(",", ":")), schema, js
    return json.dumps(schema, separators=(",", ":")), None, schema


def _validate(text: str, model: type[BaseModel] | None, js: Mapping[str, Any] | None) -> Any:
    try:
        obj = extract_json_object(text)
    except ValueError as exc:
        raise _Invalid(str(exc)) from exc
    if not isinstance(obj, dict):
        raise _Invalid("top-level value must be a JSON object")
    if model is not None:
        try:
            return model.model_validate(obj)
        except ValidationError as exc:
            detail = "; ".join(f"{'.'.join(str(p) for p in e['loc']) or '<root>'}: {e['msg']}" for e in exc.errors()[:8])
            raise _Invalid(f"schema validation failed: {detail}") from exc
    if js is not None:
        try:
            check_json_schema(obj, js)
        except (ValueError, TypeError) as exc:
            raise _Invalid(f"schema validation failed: {exc}") from exc
    return obj


class _Invalid(Exception):
    pass


Messages = list[dict[str, str]]


# --------------------------------------------------------------------------------------------------- client


class ModelClient:
    """Concrete `ModelGateway` over raw httpx. Async API + `.sync()` blocking twin. Build with
    `create_model_gateway(settings)` (or `get_llm_client`) or directly in tests.

    Legacy helpers `complete_json` / `complete_text` (A15) stay as thin wrappers over the same pipeline."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        protocol: str = "anthropic_messages",
        provider: str = "",
        timeout: float = 60.0,
        max_retries: int = 2,
        backoff_base: float = 0.5,
        max_tokens: int = 4096,
        max_concurrency: int = 4,
        cache_enabled: bool = True,
        task_overrides: str = "",
        reasoning_headroom: int = 0,
    ) -> None:
        self.protocol = normalize_model_protocol(protocol)
        self._wire = adapter_for(self.protocol)  # raises UnsupportedModelProtocol
        # Muse Spark (provider 'meta'): reasoning model. Reasoning tokens count against max_output_tokens, so add
        # headroom (MODEL_REASONING_HEADROOM) and send instructions as a `developer` message (Meta's recommendation).
        self.is_meta = provider.strip().lower() == "meta"
        self.reasoning_headroom = max(0, int(reasoning_headroom)) if self.is_meta else 0
        if self.is_meta and self.protocol == "openai_responses":
            self._wire.developer_role = True
        if self.is_meta and model.strip().lower().endswith("-contributor"):
            log.warning("llm.contributor_tier_trains_on_prompts", model=model,
                        advice="Meta may train on prompts/completions of -contributor models; do not send customer data")
        self._api_key = api_key
        self.base_url, self.model, self.provider = base_url.rstrip("/"), model, provider
        self.timeout, self.max_retries, self.backoff_base, self.max_tokens = timeout, max_retries, backoff_base, max_tokens
        self.cache_enabled, self.task_overrides = cache_enabled, task_overrides
        self.capabilities = ModelCapabilities()
        self._max_concurrency = max(1, max_concurrency)
        self._sync_sem = threading.BoundedSemaphore(self._max_concurrency)
        self._async_sems: dict[int, asyncio.Semaphore] = {}
        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._cache_lock = threading.Lock()

    def __repr__(self) -> str:  # never expose the key
        return f"ModelClient(protocol={self.protocol!r}, model={self.model!r}, base_url={self.base_url!r})"

    @property
    def name(self) -> str:
        return f"{self.protocol}:{self.model}"

    def _async_sem(self) -> asyncio.Semaphore:
        key = id(asyncio.get_running_loop())
        sem = self._async_sems.get(key)
        if sem is None:
            sem = self._async_sems[key] = asyncio.Semaphore(self._max_concurrency)
        return sem

    # ---- request / response shape ----
    def _request(self, system: str, messages: Messages, max_tokens: int, temperature: float | None) -> tuple[str, dict, dict]:
        return (self._wire.endpoint(self.base_url), self._wire.headers(self._api_key, self.base_url),
                self._wire.body(self.model, system, messages, max_tokens + self.reasoning_headroom, temperature))

    def _parse(self, resp: httpx.Response) -> tuple[str, int | None, int | None, str | None]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise LLMUnavailable("model returned a non-JSON response", retryable=True) from exc
        try:
            return self._wire.parse(data)
        except (KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMUnavailable("model response had an unexpected shape") from exc

    def _fail(self, resp: httpx.Response) -> LLMUnavailable:
        sc = resp.status_code
        msg = _error_message(resp, self._api_key)
        text = f"model returned HTTP {sc}" + (f": {msg}" if msg else "")
        low = msg.lower()
        if sc in (401, 403):
            return LLMAuthError(text, status_code=sc)
        if sc == 404 or (sc in (400, 422) and "model" in low and any(w in low for w in
                                                                    ("not found", "does not exist", "unknown", "invalid model"))):
            return LLMModelNotFound(text, status_code=sc)
        return LLMUnavailable(text, status_code=sc, retryable=sc in RETRY_STATUSES)

    def _backoff(self, attempt: int, resp: httpx.Response | None) -> float:
        ra = _retry_after(resp) if resp is not None else None
        if ra is not None:
            return ra
        return self.backoff_base * (2**attempt) * (1 + random.random() * 0.25)

    def _timeout(self, seconds: float | None = None) -> httpx.Timeout:
        t = seconds or self.timeout
        return httpx.Timeout(t, connect=min(10.0, t))

    def _log_ok(self, res: LLMResult, purpose: str, prompt_chars: int) -> None:
        status.record_success(res.input_tokens, res.output_tokens)
        log.info("llm.call", provider=self.provider, protocol=self.protocol, model=self.model, purpose=purpose, ok=True,
                 attempts=res.attempts, latency_ms=res.latency_ms, input_tokens=res.input_tokens,
                 output_tokens=res.output_tokens, stop_reason=res.stop_reason, prompt_chars=prompt_chars,
                 output_chars=len(res.text))

    def _log_err(self, exc: Exception, purpose: str, kind: str, attempts: int, started: float) -> None:
        status.record_error(str(exc), kind)
        log.warning("llm.call", provider=self.provider, protocol=self.protocol, model=self.model, purpose=purpose,
                    ok=False, error_kind=kind, error=str(exc)[:200], attempts=attempts,
                    latency_ms=int((time.perf_counter() - started) * 1000))

    @staticmethod
    def _kind(exc: LLMUnavailable) -> str:
        return "auth" if isinstance(exc, LLMAuthError) else "model_not_found" if isinstance(exc, LLMModelNotFound) \
            else "unavailable"

    # ---- transports (one HTTP exchange incl. retries) ----
    async def _send_async(self, system: str, messages: Messages, max_tokens: int, temperature: float | None,
                          purpose: str, timeout: float | None = None) -> LLMResult:
        started, attempt = time.perf_counter(), 0
        chars = len(system) + sum(len(m["content"]) for m in messages)
        last: LLMUnavailable | None = None
        async with self._async_sem(), httpx.AsyncClient(timeout=self._timeout(timeout)) as http:
            while True:
                resp: httpx.Response | None = None
                try:
                    url, headers, body = self._request(system, messages, max_tokens, temperature)
                    resp = await http.post(url, headers=headers, json=body)
                    if resp.status_code >= 400:
                        if resp.status_code == 400 and self._wire.adapt_after_400(resp.text):
                            continue
                        raise self._fail(resp)
                    text, tin, tout, stop = self._parse(resp)
                    res = LLMResult(text, tin, tout, stop, int((time.perf_counter() - started) * 1000), attempt + 1)
                    self._log_ok(res, purpose, chars)
                    return res
                except httpx.HTTPError as exc:
                    last = LLMUnavailable(f"model request failed: {type(exc).__name__}", retryable=True)
                    last.__cause__ = exc
                except LLMUnavailable as exc:
                    last = exc
                if not last.retryable or attempt >= self.max_retries:
                    self._log_err(last, purpose, self._kind(last), attempt + 1, started)
                    raise last
                await asyncio.sleep(self._backoff(attempt, resp))
                attempt += 1

    def _send_sync(self, system: str, messages: Messages, max_tokens: int, temperature: float | None,
                   purpose: str, timeout: float | None = None) -> LLMResult:
        started, attempt = time.perf_counter(), 0
        chars = len(system) + sum(len(m["content"]) for m in messages)
        last: LLMUnavailable | None = None
        with self._sync_sem, httpx.Client(timeout=self._timeout(timeout)) as http:
            while True:
                resp: httpx.Response | None = None
                try:
                    url, headers, body = self._request(system, messages, max_tokens, temperature)
                    resp = http.post(url, headers=headers, json=body)
                    if resp.status_code >= 400:
                        if resp.status_code == 400 and self._wire.adapt_after_400(resp.text):
                            continue
                        raise self._fail(resp)
                    text, tin, tout, stop = self._parse(resp)
                    res = LLMResult(text, tin, tout, stop, int((time.perf_counter() - started) * 1000), attempt + 1)
                    self._log_ok(res, purpose, chars)
                    return res
                except httpx.HTTPError as exc:
                    last = LLMUnavailable(f"model request failed: {type(exc).__name__}", retryable=True)
                    last.__cause__ = exc
                except LLMUnavailable as exc:
                    last = exc
                if not last.retryable or attempt >= self.max_retries:
                    self._log_err(last, purpose, self._kind(last), attempt + 1, started)
                    raise last
                time.sleep(self._backoff(attempt, resp))
                attempt += 1

    # ---- JSON flow: validate -> one safe repair -> typed failure (generator yields messages, receives LLMResult) ----
    def _json_flow(self, messages: Messages, schema: Schema, purpose: str) -> Generator[Messages, LLMResult, Any]:
        _, model, js = _schema_info(schema)
        res: LLMResult = yield messages
        try:
            return _validate(res.text, model, js)
        except _Invalid as first:
            log.warning("llm.invalid_output", model=self.model, purpose=purpose, reason=str(first)[:200], repairing=True)
            repair = [
                *messages,
                {"role": "assistant", "content": res.text[:6000] or "(empty)"},
                {"role": "user", "content": (
                    f"Your previous reply was rejected: {first}. Reply again with ONLY the corrected JSON object, "
                    "no prose and no markdown fences."
                )},
            ]
            res2: LLMResult = yield repair
            try:
                return _validate(res2.text, model, js)
            except _Invalid as second:
                exc = LLMInvalidOutput(f"model output invalid after repair retry: {second}", raw_preview=res2.text)
                self._log_err(exc, purpose, "invalid_output", 2, time.perf_counter())
                raise exc from second

    @staticmethod
    def _resolve_user(user: str | None, prompt: str | None, untrusted: Mapping[str, str] | None) -> str:
        task = user if user is not None else prompt
        if task is None:
            raise TypeError("complete_json requires `user=` (or `prompt=`)")
        return build_user_prompt(task, untrusted) if untrusted else task

    # ---- gateway request plumbing ----
    def _plan(self, req: ModelRequest) -> _Plan:
        d = task_defaults(req.purpose, self.task_overrides)
        msgs: Messages = [{"role": m.role, "content": m.content} for m in req.messages]
        if req.input is not None:
            msgs.append({"role": "user", "content": req.input})
        if not msgs or msgs[-1]["role"] != "user":
            raise TypeError("ModelRequest needs a final user message (messages or input)")
        if req.untrusted:
            msgs[-1] = {"role": "user", "content": build_user_prompt(msgs[-1]["content"], req.untrusted)}
        temp = d.temperature if req.temperature is None else req.temperature
        return _Plan(msgs, temp, req.max_output_tokens or d.max_output_tokens, req.timeout_s or d.timeout_s, d.deterministic)

    def _cache_key(self, req: ModelRequest, plan: _Plan, schema_json: str | None) -> str | None:
        if not (self.cache_enabled and plan.deterministic and plan.temperature == 0):
            return None
        blob = json.dumps([self.protocol, self.model, req.prompt_version, req.system, plan.messages, schema_json,
                           plan.max_tokens], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def _meta(self, req: ModelRequest, results: list[LLMResult], started: float, *, ok: bool,
              error_kind: str | None = None, cached: bool = False) -> ModelCallMeta:
        tin = [r.input_tokens for r in results if r.input_tokens is not None]
        tout = [r.output_tokens for r in results if r.output_tokens is not None]
        in_toks = sum(tin) if tin else None
        out_toks = sum(tout) if tout else None
        tier = req.tier.value if getattr(req, "tier", None) else "FAST"
        escalated = bool(req.metadata.get("escalated", False))
        cost = estimate_cost_usd(self.model, in_toks, out_toks)
        meta = ModelCallMeta(
            provider=self.provider, model=self.model, protocol=self.protocol, purpose=req.purpose.value,
            prompt_version=req.prompt_version, latency_ms=int((time.perf_counter() - started) * 1000),
            attempts=sum(r.attempts for r in results) or 1, input_tokens=in_toks,
            output_tokens=out_toks, ok=ok, error_kind=error_kind, cached=cached,
            tier=tier, escalated=escalated, estimated_cost_usd=cost)
        status.record_call(meta)
        log.info("model.call", **{k: v for k, v in meta.as_dict().items() if k != "at"}, **{
            f"tag_{k}": str(v)[:80] for k, v in req.metadata.items()})
        return meta

    @staticmethod
    def _failure_kind(exc: Exception) -> str:
        return {"LLMInvalidOutput": "invalid_output", "LLMAuthError": "auth",
                "LLMModelNotFound": "model_not_found"}.get(type(exc).__name__, "unavailable")

    # ---- ModelGateway API (async) ----
    async def generate_structured(self, request: ModelRequest, schema: Schema = None) -> ModelResponse:
        schema = schema if schema is not None else request.response_schema
        schema_json = _schema_info(schema)[0]
        plan, started, results = self._plan(request), time.perf_counter(), []
        key = self._cache_key(request, plan, schema_json)
        if (hit := self._cache_get(key)) is not None:
            return ModelResponse(hit[0], self._meta(request, [], started, ok=True, cached=True), hit[1])
        sys_prompt = build_system_prompt(request.system, schema_json)
        gen = self._json_flow(plan.messages, schema, request.purpose.value)
        messages = next(gen)
        try:
            while True:
                res = await self._send_async(sys_prompt, messages, plan.max_tokens, plan.temperature,
                                             request.purpose.value, plan.timeout)
                results.append(res)
                try:
                    messages = gen.send(res)
                except StopIteration as done:
                    parsed = done.value
                    break
        except (LLMUnavailable, LLMInvalidOutput) as exc:
            self._meta(request, results, started, ok=False, error_kind=self._failure_kind(exc))
            raise
        self._cache_put(key, (results[-1].text, parsed))
        return ModelResponse(results[-1].text, self._meta(request, results, started, ok=True), parsed,
                             results[-1].stop_reason)

    async def generate_text(self, request: ModelRequest) -> ModelResponse:
        plan, started = self._plan(request), time.perf_counter()
        try:
            res = await self._send_async(request.system, plan.messages, plan.max_tokens, plan.temperature,
                                         request.purpose.value, plan.timeout)
        except LLMUnavailable as exc:
            self._meta(request, [], started, ok=False, error_kind=self._failure_kind(exc))
            raise
        return ModelResponse(res.text, self._meta(request, [res], started, ok=True), None, res.stop_reason)

    async def health(self) -> ModelHealth:
        return status.current_health()

    def _cache_get(self, key: str | None) -> tuple[str, Any] | None:
        if key is None:
            return None
        with self._cache_lock:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
        if hit is None:
            return None
        text, parsed = hit
        return text, parsed.model_copy(deep=True) if isinstance(parsed, BaseModel) else json.loads(json.dumps(parsed))

    def _cache_put(self, key: str | None, value: tuple[str, Any]) -> None:
        if key is None:
            return
        with self._cache_lock:
            self._cache[key] = value
            while len(self._cache) > 256:
                self._cache.popitem(last=False)

    # ---- legacy A15 helpers (kept for compatibility; same pipeline underneath) ----
    async def complete_text(self, *, system: str, user: str, max_tokens: int | None = None,
                            temperature: float | None = None, purpose: str = "text") -> LLMResult:
        return await self._send_async(system, [{"role": "user", "content": user}], max_tokens or self.max_tokens,
                                      temperature, purpose)

    @overload
    async def complete_json(self, *, system: str, user: str | None = ..., prompt: str | None = ..., schema: type[T],
                            untrusted: Mapping[str, str] | None = ..., max_tokens: int | None = ...,
                            temperature: float | None = ..., purpose: str = ...) -> T: ...
    @overload
    async def complete_json(self, *, system: str, user: str | None = ..., prompt: str | None = ...,
                            schema: Mapping[str, Any] | None = ..., untrusted: Mapping[str, str] | None = ...,
                            max_tokens: int | None = ..., temperature: float | None = ...,
                            purpose: str = ...) -> dict[str, Any]: ...

    async def complete_json(self, *, system: str, user: str | None = None, prompt: str | None = None,
                            schema: Schema = None, untrusted: Mapping[str, str] | None = None,
                            max_tokens: int | None = None, temperature: float | None = None,
                            purpose: str = "json") -> Any:
        """Accepts both caller shapes: `user=` (A10 patching) and `prompt=` (A3 rca). Awaitable."""
        sys_prompt = build_system_prompt(system, _schema_info(schema)[0])
        gen = self._json_flow([{"role": "user", "content": self._resolve_user(user, prompt, untrusted)}], schema, purpose)
        messages = next(gen)
        while True:
            res = await self._send_async(sys_prompt, messages, max_tokens or self.max_tokens, temperature, purpose)
            try:
                messages = gen.send(res)
            except StopIteration as done:
                return done.value

    # ---- sync facade ----
    def sync(self) -> SyncModelClient:
        return SyncModelClient(self)


@dataclass
class _Plan:
    messages: Messages
    temperature: float | None
    max_tokens: int
    timeout: float
    deterministic: bool


class SyncModelClient:
    """Blocking facade for the sync `run_rca` / `generate_hypotheses` (which discard awaitables). Uses httpx.Client,
    so it is safe to call with or without a running event loop (it blocks the calling thread, though)."""

    def __init__(self, client: ModelClient) -> None:
        self._c = client
        self.capabilities = client.capabilities

    @property
    def name(self) -> str:
        return self._c.name

    def __repr__(self) -> str:
        return f"Sync{self._c!r}"

    def health(self) -> ModelHealth:
        return status.current_health()

    def generate_structured(self, request: ModelRequest, schema: Schema = None) -> ModelResponse:
        c = self._c
        schema = schema if schema is not None else request.response_schema
        schema_json = _schema_info(schema)[0]
        plan, started, results = c._plan(request), time.perf_counter(), []
        key = c._cache_key(request, plan, schema_json)
        if (hit := c._cache_get(key)) is not None:
            return ModelResponse(hit[0], c._meta(request, [], started, ok=True, cached=True), hit[1])
        sys_prompt = build_system_prompt(request.system, schema_json)
        gen = c._json_flow(plan.messages, schema, request.purpose.value)
        messages = next(gen)
        try:
            while True:
                res = c._send_sync(sys_prompt, messages, plan.max_tokens, plan.temperature, request.purpose.value,
                                   plan.timeout)
                results.append(res)
                try:
                    messages = gen.send(res)
                except StopIteration as done:
                    parsed = done.value
                    break
        except (LLMUnavailable, LLMInvalidOutput) as exc:
            c._meta(request, results, started, ok=False, error_kind=c._failure_kind(exc))
            raise
        c._cache_put(key, (results[-1].text, parsed))
        return ModelResponse(results[-1].text, c._meta(request, results, started, ok=True), parsed,
                             results[-1].stop_reason)

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        c = self._c
        plan, started = c._plan(request), time.perf_counter()
        try:
            res = c._send_sync(request.system, plan.messages, plan.max_tokens, plan.temperature, request.purpose.value,
                               plan.timeout)
        except LLMUnavailable as exc:
            c._meta(request, [], started, ok=False, error_kind=c._failure_kind(exc))
            raise
        return ModelResponse(res.text, c._meta(request, [res], started, ok=True), None, res.stop_reason)

    def complete_text(self, *, system: str, user: str, max_tokens: int | None = None,
                      temperature: float | None = None, purpose: str = "text") -> LLMResult:
        c = self._c
        return c._send_sync(system, [{"role": "user", "content": user}], max_tokens or c.max_tokens, temperature, purpose)

    def complete_json(self, *, system: str, user: str | None = None, prompt: str | None = None, schema: Schema = None,
                      untrusted: Mapping[str, str] | None = None, max_tokens: int | None = None,
                      temperature: float | None = None, purpose: str = "json") -> Any:
        c = self._c
        sys_prompt = build_system_prompt(system, _schema_info(schema)[0])
        gen = c._json_flow([{"role": "user", "content": c._resolve_user(user, prompt, untrusted)}], schema, purpose)
        messages = next(gen)
        while True:
            res = c._send_sync(sys_prompt, messages, max_tokens or c.max_tokens, temperature, purpose)
            try:
                messages = gen.send(res)
            except StopIteration as done:
                return done.value


class UnavailableLLM:
    """Null gateway: every call raises `LLMUnavailable`; health is NOT_CONFIGURED. Falsy so `if llm:` works."""

    name = "unavailable"
    capabilities = ModelCapabilities(structured_output=False)

    def __init__(self, reason: str = "MODEL_API_KEY not configured") -> None:
        self.reason = reason

    async def complete_json(self, **_: Any) -> Any:
        raise LLMUnavailable(self.reason)

    async def complete_text(self, **_: Any) -> Any:
        raise LLMUnavailable(self.reason)

    async def generate_structured(self, *_: Any, **__: Any) -> Any:
        raise LLMUnavailable(self.reason)

    async def generate_text(self, *_: Any, **__: Any) -> Any:
        raise LLMUnavailable(self.reason)

    async def health(self) -> ModelHealth:
        return ModelHealth(ModelHealthState.NOT_CONFIGURED, detail=self.reason)

    def sync(self) -> UnavailableLLM:
        return self

    def __bool__(self) -> bool:
        return False


# --------------------------------------------------------------------------------------------------- router


class ModelRouter(ModelClient):
    """Cost-aware model router managing FAST (default) and DEEP tiers.

    Routes semantic tasks to the cheapest reliable model (FAST tier, e.g. Claude Haiku 4.5).
    Escalates to DEEP tier (e.g. Claude Sonnet) only when complexity score exceeds the threshold
    or when FAST tier structured validation fails after retry.
    """

    def __init__(
        self,
        *,
        fast_client: ModelClient,
        deep_client: ModelClient | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.fast_client = fast_client
        self.deep_client = deep_client
        self.settings = settings or get_settings()
        self._backoff_base = fast_client.backoff_base
        super().__init__(
            api_key=fast_client._api_key,
            base_url=fast_client.base_url,
            model=fast_client.model,
            protocol=fast_client.protocol,
            provider=fast_client.provider,
            timeout=fast_client.timeout,
            max_retries=fast_client.max_retries,
            backoff_base=fast_client.backoff_base,
            max_tokens=fast_client.max_tokens,
            max_concurrency=fast_client._max_concurrency,
            cache_enabled=fast_client.cache_enabled,
            task_overrides=fast_client.task_overrides,
        )

    @property
    def backoff_base(self) -> float:
        return self._backoff_base

    @backoff_base.setter
    def backoff_base(self, value: float) -> None:
        self._backoff_base = value
        self.fast_client.backoff_base = value
        if self.deep_client:
            self.deep_client.backoff_base = value

    def route_tier(self, purpose: ModelPurpose, complexity_score: float | None = None) -> ModelTier:
        if purpose in (
            ModelPurpose.INTENT_CLASSIFICATION,
            ModelPurpose.CLAIM_EXTRACTION,
            ModelPurpose.EVIDENCE_SUMMARY,
            ModelPurpose.CLUSTER_LABEL,
            ModelPurpose.INCIDENT_SUMMARY,
            ModelPurpose.INTERVENTION_DRAFT,
            ModelPurpose.EXTRACTION,
            ModelPurpose.CLASSIFICATION,
            ModelPurpose.SUMMARY,
        ):
            return ModelTier.FAST

        if purpose in (
            ModelPurpose.HYPOTHESIS_GENERATION,
            ModelPurpose.COUNTEREVIDENCE_ASSESSMENT,
        ):
            if (
                complexity_score is not None
                and complexity_score >= self.settings.model_complexity_threshold
                and self.settings.model_escalation_enabled
                and self.deep_client is not None
            ):
                return ModelTier.DEEP
            return ModelTier.FAST

        return ModelTier.FAST

    async def generate_structured(self, request: ModelRequest, schema: Schema = None) -> ModelResponse:
        tier = request.tier or self.route_tier(request.purpose, request.complexity_score)
        if tier is ModelTier.DEEP and self.deep_client is not None:
            req = copy.copy(request)
            req.tier = ModelTier.DEEP
            return await self.deep_client.generate_structured(req, schema)

        req = copy.copy(request)
        req.tier = ModelTier.FAST
        try:
            return await self.fast_client.generate_structured(req, schema)
        except (LLMInvalidOutput, ValidationError) as exc:
            if self.settings.model_escalation_enabled and self.deep_client is not None:
                log.info("model.escalating_to_deep", reason=str(exc), purpose=req.purpose.value)
                escalated_req = copy.copy(req)
                escalated_req.tier = ModelTier.DEEP
                escalated_req.metadata = {**req.metadata, "escalated": True, "escalation_reason": str(exc)[:100]}
                status.record_escalation(req.purpose.value, str(exc)[:100])
                try:
                    return await self.deep_client.generate_structured(escalated_req, schema)
                except Exception as deep_exc:
                    log.warning("model.deep_escalation_failed", error=str(deep_exc))
                    raise exc
            raise

    async def generate_text(self, request: ModelRequest) -> ModelResponse:
        tier = request.tier or self.route_tier(request.purpose, request.complexity_score)
        if tier is ModelTier.DEEP and self.deep_client is not None:
            req = copy.copy(request)
            req.tier = ModelTier.DEEP
            return await self.deep_client.generate_text(req)
        req = copy.copy(request)
        req.tier = ModelTier.FAST
        return await self.fast_client.generate_text(req)

    def sync(self) -> SyncModelRouter:
        return SyncModelRouter(self)


class SyncModelRouter(SyncModelClient):
    """Blocking facade for ModelRouter."""

    def __init__(self, router: ModelRouter) -> None:
        super().__init__(router.fast_client)
        self._router = router

    def generate_structured(self, request: ModelRequest, schema: Schema = None) -> ModelResponse:
        tier = request.tier or self._router.route_tier(request.purpose, request.complexity_score)
        if tier is ModelTier.DEEP and self._router.deep_client is not None:
            req = copy.copy(request)
            req.tier = ModelTier.DEEP
            return self._router.deep_client.sync().generate_structured(req, schema)

        req = copy.copy(request)
        req.tier = ModelTier.FAST
        try:
            return self._router.fast_client.sync().generate_structured(req, schema)
        except (LLMInvalidOutput, ValidationError) as exc:
            if self._router.settings.model_escalation_enabled and self._router.deep_client is not None:
                log.info("model.escalating_to_deep", reason=str(exc), purpose=req.purpose.value)
                escalated_req = copy.copy(req)
                escalated_req.tier = ModelTier.DEEP
                escalated_req.metadata = {**req.metadata, "escalated": True, "escalation_reason": str(exc)[:100]}
                status.record_escalation(req.purpose.value, str(exc)[:100])
                try:
                    return self._router.deep_client.sync().generate_structured(escalated_req, schema)
                except Exception as deep_exc:
                    log.warning("model.deep_escalation_failed", error=str(deep_exc))
                    raise exc
            raise

    def generate_text(self, request: ModelRequest) -> ModelResponse:
        tier = request.tier or self._router.route_tier(request.purpose, request.complexity_score)
        if tier is ModelTier.DEEP and self._router.deep_client is not None:
            req = copy.copy(request)
            req.tier = ModelTier.DEEP
            return self._router.deep_client.sync().generate_text(req)
        req = copy.copy(request)
        req.tier = ModelTier.FAST
        return self._router.fast_client.sync().generate_text(req)


# --------------------------------------------------------------------------------------------------- factory


def create_model_gateway(settings: Settings | None = None) -> ModelRouter | UnavailableLLM:
    """The provider-neutral entry point. Not configured -> `UnavailableLLM` (health NOT_CONFIGURED); unknown protocol
    -> `UnsupportedModelProtocol`. Supports FAST and DEEP tiers via `ModelRouter`."""
    s = settings or get_settings()
    adapter_for(s.model_protocol)  # validate first so a typo is loud even without a key
    if not s.model_configured:
        return UnavailableLLM("MODEL_API_KEY not configured")
    fast_model = s.resolved_fast_model
    deep_model = s.resolved_deep_model
    fast_client = ModelClient(
        api_key=s.model_api_key, base_url=s.model_resolved_base_url, model=fast_model, protocol=s.model_protocol,
        provider=s.model_provider, max_retries=s.model_max_retries, max_concurrency=s.model_max_concurrency,
        cache_enabled=s.model_cache_enabled, task_overrides=s.model_task_overrides,
        reasoning_headroom=s.model_reasoning_headroom,
    )
    deep_client = None
    if deep_model and deep_model != fast_model:
        deep_client = ModelClient(
            api_key=s.model_api_key, base_url=s.model_resolved_base_url, model=deep_model, protocol=s.model_protocol,
            provider=s.model_provider, max_retries=s.model_max_retries, max_concurrency=s.model_max_concurrency,
            cache_enabled=s.model_cache_enabled, task_overrides=s.model_task_overrides,
            reasoning_headroom=s.model_reasoning_headroom,
        )
    return ModelRouter(fast_client=fast_client, deep_client=deep_client, settings=s)


def get_llm_client(settings: Settings | None = None) -> ModelClient | None:
    """Configured gateway, or None when not configured / protocol unsupported (callers go rules-only)."""
    try:
        gw = create_model_gateway(settings)
    except UnsupportedModelProtocol as exc:
        log.warning("llm.misconfigured", error=str(exc))
        return None
    return gw if isinstance(gw, ModelClient) else None


def get_sync_llm_client(settings: Settings | None = None) -> SyncModelClient | None:
    c = get_llm_client(settings)
    return c.sync() if c else None


def get_llm_or_unavailable(settings: Settings | None = None) -> ModelClient | UnavailableLLM:
    s = settings or get_settings()
    c = get_llm_client(s)
    if c is not None:
        return c
    return UnavailableLLM("MODEL_API_KEY not configured" if s.model_configured is False
                          else f"unsupported MODEL_API_PROTOCOL '{s.model_api_protocol}'")
