"""Provider-neutral model gateway: request/response types, purposes, capabilities, health and the `ModelGateway`
Protocol. Nothing here knows about a vendor. Domain code builds a `ModelRequest`, asks for a Pydantic schema and gets a
validated object back, or a typed error. No chain-of-thought is requested, parsed or stored."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

from app.connectors.llm.errors import LLMError

T = TypeVar("T", bound=BaseModel)


class UnsupportedModelProtocol(LLMError, ValueError):
    """MODEL_API_PROTOCOL is not one of openai_chat | openai_responses | anthropic_messages."""


class ModelTier(StrEnum):
    FAST = "FAST"
    DEEP = "DEEP"


class ModelPurpose(StrEnum):
    INTENT_CLASSIFICATION = "INTENT_CLASSIFICATION"
    CLAIM_EXTRACTION = "CLAIM_EXTRACTION"
    EVIDENCE_SUMMARY = "EVIDENCE_SUMMARY"
    HYPOTHESIS_GENERATION = "HYPOTHESIS_GENERATION"
    COUNTEREVIDENCE_ASSESSMENT = "COUNTEREVIDENCE_ASSESSMENT"
    INTERVENTION_DRAFT = "INTERVENTION_DRAFT"
    INCIDENT_SUMMARY = "INCIDENT_SUMMARY"
    CLUSTER_LABEL = "CLUSTER_LABEL"
    # Backward-compatible aliases
    EXTRACTION = "EXTRACTION"
    CLASSIFICATION = "CLASSIFICATION"
    SUMMARY = "SUMMARY"


# Rates in USD per 1M tokens: (input_price, output_price)
MODEL_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-3-5-haiku-20241022": (0.80, 4.00),
    "claude-3-5-haiku-latest": (0.80, 4.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-3-5-sonnet-20241022": (3.00, 15.00),
    "claude-3-5-sonnet-latest": (3.00, 15.00),
    "claude-3-7-sonnet-20250219": (3.00, 15.00),
    "claude-opus-5-5": (15.00, 75.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
}


def estimate_cost_usd(model: str, input_tokens: int | None, output_tokens: int | None) -> float | None:
    """Estimated call cost in USD. Telemetry only; never affects domain decision logic."""
    if input_tokens is None and output_tokens is None:
        return None
    rates = MODEL_PRICING.get(model.lower())
    if not rates:
        m = model.lower()
        if "haiku" in m:
            rates = (1.00, 5.00)
        elif "sonnet" in m:
            rates = (3.00, 15.00)
        elif "opus" in m:
            rates = (15.00, 75.00)
        else:
            return None
    cost = ((input_tokens or 0) / 1_000_000.0) * rates[0] + ((output_tokens or 0) / 1_000_000.0) * rates[1]
    return round(cost, 6)


@dataclass(frozen=True)
class TaskDefaults:
    temperature: float
    max_output_tokens: int
    timeout_s: float
    deterministic: bool = False  # may be cached


# Single source of per-task defaults. State-affecting tasks run cold; override via MODEL_TASK_OVERRIDES (JSON).
TASK_DEFAULTS: dict[ModelPurpose, TaskDefaults] = {
    ModelPurpose.INTENT_CLASSIFICATION: TaskDefaults(0.0, 512, 30.0, deterministic=True),
    ModelPurpose.CLAIM_EXTRACTION: TaskDefaults(0.0, 2048, 45.0, deterministic=True),
    ModelPurpose.EVIDENCE_SUMMARY: TaskDefaults(0.1, 1024, 45.0, deterministic=True),
    ModelPurpose.CLUSTER_LABEL: TaskDefaults(0.0, 256, 30.0, deterministic=True),
    ModelPurpose.INCIDENT_SUMMARY: TaskDefaults(0.2, 1024, 45.0, deterministic=True),
    ModelPurpose.HYPOTHESIS_GENERATION: TaskDefaults(0.1, 3072, 90.0),
    ModelPurpose.COUNTEREVIDENCE_ASSESSMENT: TaskDefaults(0.1, 2048, 60.0),
    ModelPurpose.INTERVENTION_DRAFT: TaskDefaults(0.2, 4096, 120.0),
    # Aliases
    ModelPurpose.EXTRACTION: TaskDefaults(0.0, 2048, 45.0, deterministic=True),
    ModelPurpose.CLASSIFICATION: TaskDefaults(0.0, 512, 30.0, deterministic=True),
    ModelPurpose.SUMMARY: TaskDefaults(0.3, 1024, 45.0),
}


def task_defaults(purpose: ModelPurpose, overrides_json: str = "") -> TaskDefaults:
    base = TASK_DEFAULTS.get(purpose, TASK_DEFAULTS[ModelPurpose.HYPOTHESIS_GENERATION])
    if not overrides_json:
        return base
    try:
        o = (json.loads(overrides_json) or {}).get(purpose.value) or {}
        return TaskDefaults(
            float(o.get("temperature", base.temperature)), int(o.get("max_output_tokens", base.max_output_tokens)),
            float(o.get("timeout_s", base.timeout_s)), base.deterministic,
        )
    except (ValueError, TypeError, AttributeError):
        return base  # malformed override must never take the runtime down


@dataclass(frozen=True)
class ModelMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass
class ModelRequest:
    """Provider-neutral request. `system` and `messages` are TRUSTED text; web/user content belongs in `untrusted`
    (label -> text), which is rendered into delimited data blocks appended to the last user message."""

    purpose: ModelPurpose
    system: str
    messages: list[ModelMessage] = field(default_factory=list)
    input: str | None = None  # convenience: single user message
    untrusted: Mapping[str, str] | None = None
    temperature: float | None = None  # None -> per-purpose default
    max_output_tokens: int | None = None
    timeout_s: float | None = None
    response_schema: type[BaseModel] | Mapping[str, Any] | None = None
    prompt_version: str = ""
    tier: ModelTier | None = None
    complexity_score: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)  # caller tags, e.g. incident_id; never sent to the model


@dataclass(frozen=True)
class ModelCallMeta:
    """What is persisted per call. Deliberately contains no prompt text, no output and no reasoning."""

    provider: str
    model: str
    protocol: str
    purpose: str
    prompt_version: str
    latency_ms: int
    attempts: int
    input_tokens: int | None
    output_tokens: int | None
    ok: bool
    error_kind: str | None = None
    cached: bool = False
    at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tier: str = "FAST"
    escalated: bool = False
    estimated_cost_usd: float | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        d["at"] = self.at.isoformat()
        return d


@dataclass
class ModelResponse:
    text: str
    meta: ModelCallMeta
    parsed: Any = None  # validated pydantic instance (or dict for a JSON-schema dict) for structured calls
    stop_reason: str | None = None


@dataclass(frozen=True)
class ModelCapabilities:
    structured_output: bool = True  # prompt-enforced JSON + local validation (not provider-native)
    json_schema: bool = False  # provider-native constrained decoding not used (portable across gateways)
    tool_calling: bool = False
    streaming: bool = False
    max_context: int | None = None


class ModelHealthState(StrEnum):
    NOT_CONFIGURED = "NOT_CONFIGURED"
    READY = "READY"
    AUTH_FAILED = "AUTH_FAILED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True)
class ModelHealth:
    state: ModelHealthState
    provider: str = ""
    model: str = ""
    protocol: str = ""
    detail: str | None = None
    last_success: datetime | None = None
    last_error: str | None = None


@runtime_checkable
class ModelGateway(Protocol):
    """The only seam domain code uses to reach a model. Async; `.sync()` returns a blocking twin."""

    name: str
    capabilities: ModelCapabilities

    async def generate_structured(self, request: ModelRequest, schema: type[T]) -> ModelResponse: ...

    async def generate_text(self, request: ModelRequest) -> ModelResponse: ...

    async def health(self) -> ModelHealth: ...
