"""Unit tests for cost-aware ModelRouter and FAST/DEEP tier routing."""
from __future__ import annotations

import json

import httpx
import pytest
import respx
from app.connectors.llm import (
    ModelCallMeta,
    ModelHealthState,
    ModelPurpose,
    ModelRequest,
    ModelResponse,
    ModelTier,
    create_model_gateway,
    estimate_cost_usd,
)
from app.connectors.llm import status as llm_status
from app.connectors.llm.client import ModelClient, ModelRouter
from app.connectors.llm.registry import get_prompt
from app.core.config import Settings
from app.investigation.rca import EvidenceView, HypothesisDraft, Layer, compute_rca_complexity
from pydantic import BaseModel, Field


class ClassificationResult(BaseModel):
    intent: str
    confidence: float


class HypothesisOutput(BaseModel):
    title: str
    evidence_ids: list[str] = Field(default_factory=list)


def make_settings(**kwargs) -> Settings:
    return Settings(
        model_api_key="sk-test-fake-key",
        model_api_protocol="openai_chat",
        model_base_url="https://api.test/v1",
        model_fast_name="fast-haiku",
        model_deep_name="deep-sonnet",
        model_default_tier="fast",
        model_escalation_enabled=True,
        model_complexity_threshold=0.65,
        _env_file=None,
        **kwargs,
    )


@pytest.fixture(autouse=True)
def _reset_status(monkeypatch):
    monkeypatch.setattr(llm_status, "get_settings", lambda: make_settings())
    llm_status.reset_status()
    yield
    llm_status.reset_status()


@respx.mock
async def test_router_defaults_to_fast_tier():
    route = respx.post("https://api.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"intent": "MIGRATION", "confidence": 0.95}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20},
            },
        )
    )
    s = make_settings()
    gw = create_model_gateway(s)
    assert isinstance(gw, ModelRouter)

    req = ModelRequest(
        purpose=ModelPurpose.INTENT_CLASSIFICATION,
        system="Classify intent",
        input="migrating from tool A to tool B",
    )
    res = await gw.generate_structured(req, ClassificationResult)
    assert res.parsed.intent == "MIGRATION"
    assert res.parsed.confidence == 0.95
    assert res.meta.tier == "FAST"
    assert res.meta.model == "fast-haiku"

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "fast-haiku"


@respx.mock
async def test_router_routes_to_deep_on_high_complexity():
    route = respx.post("https://api.test/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": '{"title": "Competitor gained share", "evidence_ids": ["e1"]}'}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 200, "completion_tokens": 30},
            },
        )
    )
    s = make_settings()
    gw = create_model_gateway(s)

    req = ModelRequest(
        purpose=ModelPurpose.HYPOTHESIS_GENERATION,
        system="Propose cause",
        input="Visibility down",
        complexity_score=0.85,  # Above 0.65 threshold
    )
    res = await gw.generate_structured(req, HypothesisOutput)
    assert res.parsed.title == "Competitor gained share"
    assert res.meta.tier == "DEEP"
    assert res.meta.model == "deep-sonnet"

    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "deep-sonnet"


@respx.mock
async def test_router_escalates_on_schema_failure():
    # First 2 calls to FAST fail schema validation (repair retry also fails)
    # 3rd call goes to DEEP and succeeds
    route = respx.post("https://api.test/v1/chat/completions").mock(
        side_effect=[
            httpx.Response(200, json={"choices": [{"message": {"content": "not json"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": "still not json"}}]}),
            httpx.Response(200, json={"choices": [{"message": {"content": '{"intent": "INFORMATIONAL", "confidence": 0.8}'}}]}),
        ]
    )
    s = make_settings(model_max_retries=1)
    gw = create_model_gateway(s)

    req = ModelRequest(
        purpose=ModelPurpose.INTENT_CLASSIFICATION,
        system="Classify intent",
        input="what is AEO",
    )
    res = await gw.generate_structured(req, ClassificationResult)
    assert res.parsed.intent == "INFORMATIONAL"
    assert res.meta.tier == "DEEP"
    assert res.meta.escalated is True

    # 2 calls to fast-haiku, 1 call to deep-sonnet
    models_called = [json.loads(c.request.content)["model"] for c in route.calls]
    assert models_called == ["fast-haiku", "fast-haiku", "deep-sonnet"]

    # Verify telemetry tracker
    cap = llm_status.llm_capability()
    assert cap.meta["escalations"] >= 1
    assert cap.meta["fast_calls"] >= 1
    assert cap.meta["deep_calls"] >= 1


def test_deterministic_complexity_score():
    ev_simple = [
        EvidenceView(id="e1", type="profound", status="live"),
        EvidenceView(id="e2", type="owned", status="live"),
    ]
    rules_simple = [HypothesisDraft(rule_id="r1", layer=Layer.OWNED_CONTENT, title="h1", confidence=0.7)]
    score_simple = compute_rca_complexity({}, ev_simple, rules_simple)
    assert score_simple < 0.65  # simple -> FAST

    ev_complex = [
        EvidenceView(id=f"e{i}", type="profound" if i < 3 else ("competitor" if i < 6 else ("owned" if i < 9 else "external_web")), status="live",
                     contradiction=0.6 if i == 5 else 0.0, raw={"competitor": f"comp{i%2}"})
        for i in range(16)
    ]
    rules_complex = [
        HypothesisDraft(rule_id=f"r{i}", layer=Layer.COMPETITOR, title=f"h{i}", confidence=0.8,
                        contradicting_evidence_ids=["e5"] if i == 0 else [])
        for i in range(4)
    ]
    score_complex = compute_rca_complexity({}, ev_complex, rules_complex)
    assert score_complex >= 0.65  # complex -> DEEP


def test_cost_telemetry_estimation():
    # Haiku pricing: $1/M in, $5/M out
    cost_haiku = estimate_cost_usd("claude-haiku-4-5", 1000, 200)
    expected_haiku = round((1000 / 1_000_000) * 1.0 + (200 / 1_000_000) * 5.0, 6)
    assert cost_haiku == expected_haiku

    # Sonnet pricing: $3/M in, $15/M out
    cost_sonnet = estimate_cost_usd("claude-sonnet-4-6", 1000, 200)
    expected_sonnet = round((1000 / 1_000_000) * 3.0 + (200 / 1_000_000) * 15.0, 6)
    assert cost_sonnet == expected_sonnet
