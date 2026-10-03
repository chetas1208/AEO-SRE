"""Adapter around G2's pure contradiction module (check 3). Never claims a semantic pass that did not run."""
from __future__ import annotations

import asyncio
import functools
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import structlog

from app.changeguard.decision import (
    CANONICAL_CONFLICT,
    CANONICAL_UNAVAILABLE,
    CANONICAL_UNCERTAIN,
    NO_CLAIMS,
    SEMANTIC_DEGRADED,
    Decision,
    Finding,
)

log = structlog.get_logger()


@dataclass
class SemanticOutcome:
    findings: list[Finding] = field(default_factory=list)
    state: str = "ok"  # ok | degraded | skipped_no_canonical_truth
    detail: dict[str, Any] = field(default_factory=dict)


@functools.lru_cache(maxsize=1)
def _ranker() -> Any | None:
    try:
        from app.evidence.ranker import EvidenceRanker

        return EvidenceRanker.load()
    except Exception as exc:  # noqa: BLE001
        log.info("changeguard.ranker_unavailable", error=type(exc).__name__)
        return None


def _gateway() -> Any | None:
    try:
        from app.connectors.llm.client import create_model_gateway
        from app.core.config import get_settings

        return create_model_gateway(get_settings())
    except Exception as exc:  # noqa: BLE001
        log.info("changeguard.gateway_unavailable", error=type(exc).__name__)
        return None


def canonical_rows(rows: list[Any]) -> list[dict[str, Any]]:
    return [{"id": str(r.id), "key": r.key, "statement": r.statement, "status": r.status, "scope": r.scope,
             "entities": list(r.entities or []), "valid_from": r.valid_from, "valid_until": r.valid_until} for r in rows]


def _run_g2(claim_texts: list[str], canonical: list[dict[str, Any]], now: datetime) -> tuple[Any, dict[str, str]]:
    from app.changeguard.claims import extract_claims
    from app.changeguard.contradiction import evaluate_canonical

    proposed = extract_claims(claim_texts)
    texts = {c.id: c.text for c in proposed}
    res = evaluate_canonical(proposed, canonical, gateway=_gateway(), ranker=_ranker(), now=now)
    return res, texts


async def evaluate(claim_texts: list[str], canonical: list[dict[str, Any]], now: datetime) -> SemanticOutcome:
    """Run G2's evaluation off the event loop. `canonical` = rows from `canonical_rows` (already org/scope filtered)."""
    if not canonical:
        return SemanticOutcome(
            [Finding(CANONICAL_UNAVAILABLE, Decision.ALLOW,
                     "no canonical truth is defined for this organization: the canonical-conflict check was skipped, "
                     "not passed", details={"claims_checked": 0})],
            "skipped_no_canonical_truth")
    if not claim_texts:
        return SemanticOutcome(
            [Finding(NO_CLAIMS, Decision.ALLOW, "the change states no claims, so there was nothing to compare with "
                     "canonical truth", details={"canonical_claims": len(canonical)})], "ok")
    try:
        res, texts = await asyncio.to_thread(_run_g2, claim_texts, canonical, now)
    except Exception as exc:  # noqa: BLE001  - a failing check must be visible, never a silent pass
        log.warning("changeguard.semantic_failed", error=type(exc).__name__)
        return SemanticOutcome(
            [Finding(SEMANTIC_DEGRADED, Decision.ALLOW,
                     f"the canonical-truth check could not run ({type(exc).__name__}); no semantic pass is claimed",
                     details={"error": type(exc).__name__})], "degraded")
    by_id = {c["id"]: c for c in canonical}
    out = SemanticOutcome(state=res.semantic_check, detail={
        "pairs_compared": res.pairs_compared, "model_used": res.model_used, "ranker_used": res.ranker_used,
        "ignored_canonical": res.ignored_canonical, "unresolved_pairs": len(res.unresolved_pairs)})
    for f in res.findings:
        canon = by_id.get(f.canonical_claim_id, {})
        conflict = f.type == "canonical_conflict"
        stmt = canon.get("statement", "")
        proposed = texts.get(f.proposed_claim_id, "")
        out.findings.append(Finding(
            CANONICAL_CONFLICT if conflict else CANONICAL_UNCERTAIN,
            Decision.BLOCK if conflict else Decision.REQUIRE_REVIEW,
            (f"proposed claim {proposed!r} contradicts canonical claim {canon.get('key')!r} ({stmt!r})"
             if conflict else
             f"proposed claim {proposed!r} may contradict canonical claim {canon.get('key')!r} ({stmt!r}); "
             f"a human must decide"),
            references={"canonical_claim_id": f.canonical_claim_id, "canonical_claim_key": canon.get("key")},
            details={"proposed_claim": proposed, "canonical_statement": stmt, "relation": f.relation,
                     "reasons": f.reasons, "confidence": f.confidence, "source": f.source}))
    if res.semantic_check == "degraded":
        out.findings.append(Finding(
            SEMANTIC_DEGRADED, Decision.ALLOW,
            "semantic layers did not fully run (" + ", ".join(res.degraded_reasons or ["unspecified"]) +
            "): deterministic rules decided; no semantic pass is claimed",
            details={"degraded_reasons": res.degraded_reasons}))
    return out
