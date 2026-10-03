"""Compare AI-perceived claims with ACTIVE canonical claims (G2's `evaluate_canonical`); produce DiscoveryGap findings.

Perceived claims play the role of "proposed" claims. Deterministic CONFLICTING is final; ambiguous pairs may use the
ranker/model exactly as G2 defines (the model only classifies supplied claim pairs; unknown ids are rejected there and
re-checked here). A degraded semantic layer is reported, never hidden, and no result is ever a silent pass.
"""
from __future__ import annotations

import hashlib
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import structlog

from app.changeguard.claims import extract_claims
from app.changeguard.contradiction import evaluate_canonical
from app.discovery_gap.perception import ORIGIN_ANSWER, PerceivedClaim

log = structlog.get_logger()


@dataclass
class DiscoveryGap:
    gap_key: str
    canonical_claim_id: str
    canonical_key: str | None
    canonical_statement: str
    perceived_claim: str
    kind: str  # conflict | uncertain
    relation: str
    confidence: float
    reasons: list[str]
    decided_by: str  # rules | ranker | model
    engines: list[str]
    prompts: list[str]
    occurrence: int
    observations: int
    citation_domains: list[dict[str, Any]]
    citation_urls: list[str]
    first_observed_at: datetime | None
    last_observed_at: datetime | None
    origin: str
    evidence_grade: str  # factcheck | answer_text_lower_confidence
    source_mode: str
    semantic_check: str
    reasoning: str | None = None
    perceived_evidence: Any = None
    window: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        d = {k: getattr(self, k) for k in self.__dataclass_fields__}
        for k in ("first_observed_at", "last_observed_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass
class AnalysisResult:
    gaps: list[DiscoveryGap] = field(default_factory=list)
    semantic_check: str = "ok"  # ok | degraded | skipped_no_canonical_truth
    degraded_reasons: list[str] = field(default_factory=list)
    ignored_canonical: dict[str, int] = field(default_factory=dict)
    pairs_compared: int = 0
    perceived_unique: int = 0
    model_used: bool = False
    ranker_used: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {"semantic_check": self.semantic_check, "degraded_reasons": self.degraded_reasons,
                "ignored_canonical": self.ignored_canonical, "pairs_compared": self.pairs_compared,
                "perceived_unique": self.perceived_unique, "model_used": self.model_used,
                "ranker_used": self.ranker_used, "gaps": len(self.gaps)}


def gap_key(org_id: Any, canonical_claim_id: str, perceived_key: str) -> str:
    return hashlib.sha256(f"{org_id}|{canonical_claim_id}|{perceived_key}".encode()).hexdigest()[:24]


def _group(perceived: list[PerceivedClaim]) -> dict[str, list[PerceivedClaim]]:
    groups: dict[str, list[PerceivedClaim]] = {}
    for p in perceived:
        groups.setdefault(p.key, []).append(p)
    return groups


def default_gateway() -> Any | None:
    try:
        from app.connectors.llm.client import create_model_gateway
        from app.core.config import get_settings

        return create_model_gateway(get_settings())
    except Exception as exc:  # noqa: BLE001
        log.info("discovery_gap.gateway_unavailable", error=type(exc).__name__)
        return None


def default_ranker() -> Any | None:
    try:
        from app.evidence.ranker import EvidenceRanker

        return EvidenceRanker.load()
    except Exception as exc:  # noqa: BLE001
        log.info("discovery_gap.ranker_unavailable", error=type(exc).__name__)
        return None


def check_canonical_available(canonical_rows: list[dict[str, Any]], now: datetime | None = None) -> tuple[bool, dict[str, int]]:
    """Cheap pre-check (no Profound call): is any ACTIVE, currently valid canonical claim left?"""
    res = evaluate_canonical([], canonical_rows, now=now)
    return res.semantic_check != "skipped_no_canonical_truth", res.ignored_canonical


def analyze(org_id: Any, perceived: list[PerceivedClaim], canonical_rows: list[dict[str, Any]], *, gateway: Any = None,
            ranker: Any = None, now: datetime | None = None) -> AnalysisResult:
    """Synchronous (the semantic layer blocks on the model); call via asyncio.to_thread from async code."""
    now = now or datetime.now(UTC)
    groups = _group(perceived)
    items = [{"id": f"pc_{k}", "text": ps[0].text} for k, ps in groups.items()]
    proposed = extract_claims(items)
    res = evaluate_canonical(proposed, canonical_rows, gateway=gateway, ranker=ranker, now=now)
    out = AnalysisResult(semantic_check=res.semantic_check, degraded_reasons=list(res.degraded_reasons),
                         ignored_canonical=dict(res.ignored_canonical), pairs_compared=res.pairs_compared,
                         perceived_unique=len(groups), model_used=res.model_used, ranker_used=res.ranker_used)
    canon = {str(r.get("id")): r for r in canonical_rows}
    by_pid = {f"pc_{k}": ps for k, ps in groups.items()}
    for f in res.findings:
        ps, row = by_pid.get(f.proposed_claim_id), canon.get(f.canonical_claim_id)
        if ps is None or row is None:  # an id the engine was never given: reject, never persist
            log.warning("discovery_gap.unknown_id_rejected", proposed=f.proposed_claim_id,
                        canonical=f.canonical_claim_id)
            continue
        lower = all(p.origin == ORIGIN_ANSWER for p in ps)
        conf = float(f.confidence)
        caps = [p.confidence_cap for p in ps if p.confidence_cap is not None]
        if lower and caps:
            conf = min(conf, max(caps))
        engines = sorted({e for p in ps for e in p.engines})
        cites: Counter[str] = Counter()
        urls: list[str] = []
        for p in ps:
            for s in p.citation_sources:
                cites[s["domain"]] += 1
                if s.get("url") and s["url"] not in urls:
                    urls.append(s["url"])
        times = [p.observed_at for p in ps if p.observed_at]
        first = ps[0]
        out.gaps.append(DiscoveryGap(
            gap_key=gap_key(org_id, f.canonical_claim_id, first.key), canonical_claim_id=f.canonical_claim_id,
            canonical_key=row.get("key"), canonical_statement=str(row.get("statement") or ""),
            perceived_claim=first.text, kind="conflict" if f.type == "canonical_conflict" and not lower else "uncertain",
            relation=f.relation, confidence=round(conf, 3), reasons=list(f.reasons), decided_by=f.source,
            engines=engines, prompts=sorted({p.prompt for p in ps if p.prompt})[:10],
            occurrence=sum(p.occurrence for p in ps), observations=len(ps),
            citation_domains=[{"domain": d, "count": n} for d, n in cites.most_common(10)], citation_urls=urls[:10],
            first_observed_at=min(times) if times else None, last_observed_at=max(times) if times else None,
            origin=first.origin, evidence_grade="answer_text_lower_confidence" if lower else "factcheck",
            source_mode=first.source_mode if len({p.source_mode for p in ps}) == 1 else "MIXED",
            semantic_check=res.semantic_check, reasoning=next((p.reasoning for p in ps if p.reasoning), None),
            perceived_evidence=next((p.evidence for p in ps if p.evidence), None),
            window=list(first.window) if first.window else None))
    out.gaps.sort(key=lambda g: (g.kind != "conflict", -g.occurrence, -g.confidence, g.gap_key))
    return out
