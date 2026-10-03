"""Smallest safe live model check. Run once MODEL_API_KEY (+ protocol/base URL/name) is set.

    cd backend && .venv/bin/python -m app.devtools.model_smoke

1. validate configuration (protocol supported, key present) without printing the key;
2. ONE tiny structured request (a few tokens), validated against a Pydantic schema;
3. print provider / model / protocol / latency / tokens (key redacted) and the health state.

Exit codes: 0 READY, 1 degraded (call failed or invalid output), 2 not configured / unsupported protocol, 3 auth failed.
Nothing is called when not configured.
"""
from __future__ import annotations

import asyncio
import sys

from pydantic import BaseModel

from app.connectors.llm import (
    LLMAuthError,
    LLMError,
    ModelHealthState,
    ModelPurpose,
    ModelRequest,
    ModelTier,
    UnsupportedModelProtocol,
    create_model_gateway,
)
from app.core.config import get_settings


class SmokeIntent(BaseModel):
    intent: str
    confidence: float


EXIT = {ModelHealthState.READY: 0, ModelHealthState.DEGRADED: 1, ModelHealthState.NOT_CONFIGURED: 2,
        ModelHealthState.AUTH_FAILED: 3}


def _report(lines: dict[str, object]) -> None:
    for k, v in lines.items():
        print(f"{k:<12} {v}")


async def run() -> int:
    s = get_settings()
    cfg = {
        "provider": s.model_provider or "(unset)",
        "tier": "FAST",
        "model": s.resolved_fast_model,
        "protocol": s.model_protocol,
        "base_url": s.model_resolved_base_url,
        "api_key": "set (redacted)" if s.model_api_key else "NOT SET",
    }
    try:
        gw = create_model_gateway(s)
    except UnsupportedModelProtocol as exc:
        _report({**cfg, "health": ModelHealthState.NOT_CONFIGURED.value, "detail": str(exc)})
        return EXIT[ModelHealthState.NOT_CONFIGURED]
    if not s.model_configured:
        _report({**cfg, "health": ModelHealthState.NOT_CONFIGURED.value,
                 "detail": "MODEL_API_KEY not configured; no call made"})
        return EXIT[ModelHealthState.NOT_CONFIGURED]
    req = ModelRequest(
        purpose=ModelPurpose.INTENT_CLASSIFICATION,
        tier=ModelTier.FAST,
        system='Classify the search prompt into one of: INFORMATIONAL, COMPARISON, MIGRATION. Respond with JSON: {"intent": "...", "confidence": 0.0-1.0}',
        input="best CRM for enterprise migration",
        prompt_version="smoke_intent_v1",
        max_output_tokens=64,
    )
    try:
        resp = await gw.generate_structured(req, SmokeIntent)
    except LLMAuthError as exc:
        _report({**cfg, "health": ModelHealthState.AUTH_FAILED.value, "detail": str(exc)})
        return EXIT[ModelHealthState.AUTH_FAILED]
    except LLMError as exc:
        _report({**cfg, "health": ModelHealthState.DEGRADED.value, "detail": f"{type(exc).__name__}: {exc}"})
        return EXIT[ModelHealthState.DEGRADED]
    m = resp.meta
    _report({
        **cfg,
        "health": ModelHealthState.READY.value,
        "latency_ms": m.latency_ms,
        "tokens": f"in={m.input_tokens} out={m.output_tokens}",
        "cost_usd": f"${m.estimated_cost_usd:.6f}" if m.estimated_cost_usd is not None else "n/a",
        "validated": resp.parsed.model_dump(),
    })
    return 0


def main() -> None:
    sys.exit(asyncio.run(run()))


if __name__ == "__main__":
    main()
