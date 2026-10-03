"""In-process record of LLM call outcomes for the system status route (per process; workers and API each keep
their own). `llm_capability()` is the stable entry point A12's capabilities service may import."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.connectors.llm.gateway import ModelCallMeta, ModelHealth, ModelHealthState
from app.core.config import get_settings
from app.domain.enums import CapabilityState

SUPPORTED_PROTOCOLS = ("anthropic_messages", "openai_chat", "openai_responses")


@dataclass
class LLMCapability:
    """Duck-compatible with `app.schemas.system.Capability` (`Capability(**cap.as_dict())`)."""

    state: CapabilityState
    detail: str | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    meta: dict[str, Any] = field(default_factory=dict)
    key: str = "llm"
    label: str = "LLM provider"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


class _Tracker:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            self.last_success: datetime | None = None
            self.last_error: str | None = None
            self.last_error_at: datetime | None = None
            self.last_error_kind: str | None = None  # "unavailable" | "invalid_output"
            self.successes = 0
            self.failures = 0
            self.fast_calls = 0
            self.deep_calls = 0
            self.escalations = 0
            self.estimated_cost_usd = 0.0
            self.input_tokens = 0
            self.output_tokens = 0
            self.recent: deque[ModelCallMeta] = deque(maxlen=200)  # per-call metadata, never prompts/outputs
            self.by_purpose: dict[str, dict[str, float]] = {}

    def call(self, meta: ModelCallMeta) -> None:
        with self._lock:
            self.recent.append(meta)
            if getattr(meta, "tier", "FAST") == "DEEP":
                self.deep_calls += 1
            else:
                self.fast_calls += 1
            if getattr(meta, "escalated", False):
                self.escalations += 1
            if getattr(meta, "estimated_cost_usd", None):
                self.estimated_cost_usd += meta.estimated_cost_usd or 0.0
            c = self.by_purpose.setdefault(meta.purpose, {"calls": 0, "failures": 0, "latency_ms": 0})
            c["calls"] += 1
            c["failures"] += 0 if meta.ok else 1
            c["latency_ms"] += meta.latency_ms

    def record_escalation(self, purpose: str, reason: str) -> None:
        with self._lock:
            self.escalations += 1

    def success(self, input_tokens: int | None, output_tokens: int | None) -> None:
        with self._lock:
            self.last_success = datetime.now(UTC)
            self.successes += 1
            self.input_tokens += input_tokens or 0
            self.output_tokens += output_tokens or 0
            self.last_error_kind = None

    def error(self, message: str, kind: str) -> None:
        with self._lock:
            self.last_error, self.last_error_at, self.last_error_kind = message[:300], datetime.now(UTC), kind
            self.failures += 1


tracker = _Tracker()


def record_success(input_tokens: int | None = None, output_tokens: int | None = None) -> None:
    tracker.success(input_tokens, output_tokens)


def record_error(message: str, kind: str = "unavailable") -> None:
    tracker.error(message, kind)


def record_call(meta: ModelCallMeta) -> None:
    tracker.call(meta)


def record_escalation(purpose: str, reason: str) -> None:
    tracker.record_escalation(purpose, reason)


def recent_calls(*, clear: bool = False) -> list[ModelCallMeta]:
    """Per-call metadata (provider/model/protocol/purpose/prompt version/latency/tokens). `clear=True` drains the
    buffer so a pipeline stage can persist exactly the calls it made."""
    with tracker._lock:
        out = list(tracker.recent)
        if clear:
            tracker.recent.clear()
    return out


def reset_status() -> None:
    tracker.reset()


def current_health() -> ModelHealth:
    """Passive health from configuration + the outcome of the most recent calls in this process."""
    s = get_settings()
    base = {"provider": s.model_provider, "model": s.resolved_fast_model, "protocol": s.model_protocol}
    if not s.model_configured:
        return ModelHealth(ModelHealthState.NOT_CONFIGURED, detail="MODEL_API_KEY not configured; rules-only mode", **base)
    if s.model_protocol not in SUPPORTED_PROTOCOLS:
        return ModelHealth(ModelHealthState.NOT_CONFIGURED, detail=f"unsupported MODEL_API_PROTOCOL '{s.model_api_protocol}'",
                           **base)
    t = tracker
    with t._lock:
        kind, last_ok, err = t.last_error_kind, t.last_success, t.last_error
    if kind == "auth":
        return ModelHealth(ModelHealthState.AUTH_FAILED, detail="provider rejected the API key", last_success=last_ok,
                           last_error=err, **base)
    if kind is not None:
        return ModelHealth(ModelHealthState.DEGRADED, detail="most recent model call failed", last_success=last_ok,
                           last_error=err, **base)
    return ModelHealth(ModelHealthState.READY, last_success=last_ok,
                       detail=None if last_ok else "credentials configured; no model call made yet", **base)


def llm_capability() -> LLMCapability:
    """unavailable: not configured / bad protocol / auth failed / failing with no success yet; degraded: latest call
    failed (or produced invalid output) after earlier success; healthy: configured and latest call (if any) fine."""
    s = get_settings()
    h = current_health()
    meta: dict[str, Any] = {
        "model": s.resolved_fast_model,
        "fast_model": s.resolved_fast_model,
        "deep_model": s.resolved_deep_model,
        "protocol": s.model_protocol,
        "provider": s.model_provider,
        "health": h.state.value,
    }
    if h.state is ModelHealthState.NOT_CONFIGURED:
        return LLMCapability(CapabilityState.UNAVAILABLE, h.detail, meta=meta)
    t = tracker
    with t._lock:
        meta.update(
            verified_by_call=t.last_success is not None,
            calls_ok=t.successes,
            calls_failed=t.failures,
            fast_calls=t.fast_calls,
            deep_calls=t.deep_calls,
            escalations=t.escalations,
            input_tokens=t.input_tokens,
            output_tokens=t.output_tokens,
            estimated_cost_usd=round(t.estimated_cost_usd, 6),
            by_purpose={k: dict(v) for k, v in t.by_purpose.items()},
        )
        common = {"last_success": t.last_success, "last_error": t.last_error, "last_error_at": t.last_error_at,
                  "meta": meta}
        kind = t.last_error_kind
    if h.state is ModelHealthState.AUTH_FAILED:
        return LLMCapability(CapabilityState.UNAVAILABLE, h.detail, **common)
    if kind is not None:
        if kind == "invalid_output" or t.last_success is not None:
            return LLMCapability(CapabilityState.DEGRADED, "most recent model call failed", **common)
        return LLMCapability(CapabilityState.UNAVAILABLE, "model calls are failing", **common)
    if t.last_success is None:
        return LLMCapability(CapabilityState.HEALTHY, "credentials configured; no model call made yet", **common)
    return LLMCapability(CapabilityState.HEALTHY, None, **common)
