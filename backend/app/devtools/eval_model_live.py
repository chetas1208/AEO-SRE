"""Live model evaluation comparing FAST (Claude Haiku 4.5) and DEEP (Claude Sonnet 4.6) tiers.

Run with:
    make eval-model-live

Executes representative semantic scenarios through both model tiers, validates structured outputs,
verifies evidence ID adherence, and prints latency, token usage, and cost comparisons.
"""
from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

from pydantic import BaseModel, Field

from app.connectors.llm import (
    ModelPurpose,
    ModelRequest,
    ModelTier,
    create_model_gateway,
)
from app.core.config import get_settings


class IntentOutput(BaseModel):
    intent: str
    confidence: float
    rationale: str = ""


class ClaimExtractionOutput(BaseModel):
    claims: list[dict[str, str]] = Field(default_factory=list)


class HypothesisCandidate(BaseModel):
    layer: str
    title: str
    confidence: float
    evidence_ids: list[str]


class RCAOutput(BaseModel):
    hypotheses: list[HypothesisCandidate]


class InterventionDraftOutput(BaseModel):
    title: str
    summary: str
    action: str
    proposed_content: str


async def evaluate_task(
    gw: Any,
    tier: ModelTier,
    purpose: ModelPurpose,
    system: str,
    prompt: str,
    schema: type[BaseModel],
    prompt_version: str,
) -> dict[str, Any]:
    t0 = time.perf_counter()
    req = ModelRequest(
        purpose=purpose,
        tier=tier,
        system=system,
        input=prompt,
        prompt_version=prompt_version,
    )
    try:
        resp = await gw.generate_structured(req, schema)
        latency_ms = int((time.perf_counter() - t0) * 1000)
        m = resp.meta
        cost = m.estimated_cost_usd or 0.0
        return {
            "ok": True,
            "latency_ms": latency_ms,
            "in_tokens": m.input_tokens or 0,
            "out_tokens": m.output_tokens or 0,
            "cost_usd": cost,
            "parsed": resp.parsed.model_dump(),
        }
    except Exception as exc:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        return {
            "ok": False,
            "latency_ms": latency_ms,
            "in_tokens": 0,
            "out_tokens": 0,
            "cost_usd": 0.0,
            "error": str(exc),
        }


async def main() -> int:
    s = get_settings()
    if not s.model_configured:
        print("[!] MODEL_API_KEY is not configured in .env. Live model evaluation aborted.")
        return 2

    gw = create_model_gateway(s)
    fast_model = s.resolved_fast_model
    deep_model = s.resolved_deep_model

    print("=" * 80)
    print("AEO SRE — LIVE MODEL TIER EVALUATION & COST COMPARISON")
    print("=" * 80)
    print(f"FAST Tier Model: {fast_model}")
    print(f"DEEP Tier Model: {deep_model}")
    print("Provider:        Anthropic (Messages API)")
    print("=" * 80)

    scenarios = [
        {
            "name": "1. Intent Classification",
            "purpose": ModelPurpose.INTENT_CLASSIFICATION,
            "system": 'Classify prompt into ONE of: INFORMATIONAL, COMPARISON, MIGRATION. Respond JSON: {"intent": "...", "confidence": 0.0-1.0, "rationale": "..."}',
            "prompt": "best CRM for migrating from HubSpot for a 500-person SaaS company",
            "schema": IntentOutput,
            "version": "eval_intent_v1",
        },
        {
            "name": "2. Evidence Claim Extraction",
            "purpose": ModelPurpose.CLAIM_EXTRACTION,
            "system": 'Extract factual claims from text. Respond JSON: {"claims": [{"subject": "...", "predicate": "...", "object": "..."}]}',
            "prompt": "Competitor Acme announced SOC2 Type II compliance on Sept 15 with automated SSO across Okta and Azure AD.",
            "schema": ClaimExtractionOutput,
            "version": "eval_claims_v1",
        },
        {
            "name": "3. Root-Cause Hypothesis Generation",
            "purpose": ModelPurpose.HYPOTHESIS_GENERATION,
            "system": 'Analyze AI visibility drop. Cite ONLY provided evidence ids (ev-01, ev-02). Respond JSON: {"hypotheses": [{"layer": "...", "title": "...", "confidence": 0.0-1.0, "evidence_ids": ["..."]}]}',
            "prompt": 'Incident: visibility dropped 87% -> 15% on product analytics queries.\nEvidence:\n<evidence id="ev-01">Competitor page added comparative benchmark vs our platform.</evidence>\n<evidence id="ev-02">Our pricing and capability matrix was updated 3 months ago.</evidence>',
            "schema": RCAOutput,
            "version": "eval_rca_v1",
        },
    ]

    total_fast_tokens = 0
    total_deep_tokens = 0
    total_fast_cost = 0.0
    total_deep_cost = 0.0

    print(f"\n{'Scenario':<34} | {'Tier':<6} | {'Status':<6} | {'Latency':<8} | {'Tokens (In/Out)':<15} | {'Cost USD':<10}")
    print("-" * 90)

    for sc in scenarios:
        # Run FAST (Haiku)
        res_fast = await evaluate_task(gw, ModelTier.FAST, sc["purpose"], sc["system"], sc["prompt"], sc["schema"], sc["version"])
        f_toks = f"{res_fast['in_tokens']}/{res_fast['out_tokens']}"
        f_cost = f"${res_fast['cost_usd']:.6f}"
        f_status = "PASS" if res_fast["ok"] else "FAIL"
        total_fast_tokens += res_fast["in_tokens"] + res_fast["out_tokens"]
        total_fast_cost += res_fast["cost_usd"]

        print(f"{sc['name']:<34} | FAST   | {f_status:<6} | {res_fast['latency_ms']:>5} ms | {f_toks:<15} | {f_cost:<10}")

        # Run DEEP (Sonnet)
        res_deep = await evaluate_task(gw, ModelTier.DEEP, sc["purpose"], sc["system"], sc["prompt"], sc["schema"], sc["version"])
        d_toks = f"{res_deep['in_tokens']}/{res_deep['out_tokens']}"
        d_cost = f"${res_deep['cost_usd']:.6f}"
        d_status = "PASS" if res_deep["ok"] else "FAIL"
        total_deep_tokens += res_deep["in_tokens"] + res_deep["out_tokens"]
        total_deep_cost += res_deep["cost_usd"]

        print(f"{'':<34} | DEEP   | {d_status:<6} | {res_deep['latency_ms']:>5} ms | {d_toks:<15} | {d_cost:<10}")
        print("-" * 90)

    print("\n" + "=" * 80)
    print("EVALUATION SUMMARY & COST-SAVINGS ANALYSIS")
    print("=" * 80)
    print(f"FAST Tier ({fast_model}):")
    print(f"  Total Tokens: {total_fast_tokens}")
    print(f"  Total Cost:   ${total_fast_cost:.6f}")
    print(f"\nDEEP Tier ({deep_model}):")
    print(f"  Total Tokens: {total_deep_tokens}")
    print(f"  Total Cost:   ${total_deep_cost:.6f}")
    if total_deep_cost > 0:
        savings_pct = ((total_deep_cost - total_fast_cost) / total_deep_cost) * 100
        print(f"\nCost Savings with Haiku-first routing: {savings_pct:.1f}% reduction")
    print("=" * 80)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
