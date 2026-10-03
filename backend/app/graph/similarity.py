"""Similar historical contexts (spec question 3 / 7). Pure Python: Jaccard / equality overlap, no GDS.

A context is described by a `ContextSignature`. Similarity of two signatures is a weighted mean over the dimensions
for which the FOCAL signature has information; equality dimensions score 0/1, set dimensions use Jaccard (two empty
sets agree: 1.0, one empty: 0.0). Ordering is deterministic (score desc, occurred_at desc, id).
"""
from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from app.graph.results import DecisionStats, SimilarContext

ALLOW_DECISIONS = frozenset({"ALLOW", "MERGE"})
REVIEW_DECISIONS = frozenset({"REQUIRE_REVIEW", "DELAY"})
BLOCK_DECISIONS = frozenset({"BLOCK"})

# dimension -> weight (spec: agent type, action type, target type, claim types, conflict type, experiment overlap,
# prompt cluster; the candidate's decision is what we aggregate, so it is not a matching dimension)
WEIGHTS: dict[str, float] = {
    "agent_type": 1.0, "action_type": 2.0, "target_type": 1.0, "claim_types": 1.5, "conflict_types": 2.0,
    "experiments": 1.5, "prompt_clusters": 1.0,
}
SCALAR_DIMS = ("agent_type", "action_type", "target_type")
SET_DIMS = ("claim_types", "conflict_types", "experiments", "prompt_clusters")
DEFAULT_MIN_SCORE = 0.5
DEFAULT_TOP_K = 25


@dataclass(frozen=True)
class ContextSignature:
    agent_type: str | None = None
    action_type: str | None = None
    target_type: str | None = None
    claim_types: frozenset[str] = frozenset()
    conflict_types: frozenset[str] = frozenset()
    experiments: frozenset[str] = frozenset()
    prompt_clusters: frozenset[str] = frozenset()
    # set dimensions are "known" for the focal context only when listed here (e.g. conflicts are known after detection)
    known_sets: frozenset[str] = frozenset(SET_DIMS)


@dataclass(frozen=True)
class Candidate:
    changeset_id: str
    signature: ContextSignature
    decision: str | None
    decision_id: str | None = None
    occurred_at: datetime | None = None
    outcome: str | None = None
    reward: float | None = None
    extra: dict = field(default_factory=dict, compare=False)


def jaccard(a: Iterable[str], b: Iterable[str]) -> float:
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    union = sa | sb
    return len(sa & sb) / len(union) if union else 1.0


def similarity(focal: ContextSignature, cand: ContextSignature) -> tuple[float, dict[str, float]]:
    """Weighted mean of per-dimension similarities over dimensions the focal context knows about."""
    parts: dict[str, float] = {}
    for dim in SCALAR_DIMS:
        f = getattr(focal, dim)
        if f is None:
            continue
        parts[dim] = 1.0 if f == getattr(cand, dim) else 0.0
    for dim in SET_DIMS:
        if dim not in focal.known_sets:
            continue
        parts[dim] = round(jaccard(getattr(focal, dim), getattr(cand, dim)), 6)
    if not parts:
        return 0.0, {}
    total = sum(WEIGHTS[d] for d in parts)
    score = sum(WEIGHTS[d] * v for d, v in parts.items()) / total
    return round(score, 6), parts


def rank_similar(focal: ContextSignature, candidates: Sequence[Candidate], *, min_score: float = DEFAULT_MIN_SCORE,
                 top_k: int = DEFAULT_TOP_K) -> list[SimilarContext]:
    scored: list[tuple[float, Candidate, dict[str, float]]] = []
    for c in candidates:
        s, parts = similarity(focal, c.signature)
        if s >= min_score and parts:
            scored.append((s, c, parts))
    scored.sort(key=lambda x: (-x[0], -(x[1].occurred_at.timestamp() if x[1].occurred_at else 0.0), x[1].changeset_id))
    return [
        SimilarContext(changeset_id=c.changeset_id, decision=c.decision, decision_id=c.decision_id, score=s,
                       matched=parts, occurred_at=c.occurred_at, outcome=c.outcome, reward=c.reward)
        for s, c, parts in scored[:top_k]
    ]


def decision_rates(contexts: Sequence[SimilarContext]) -> dict[str, float | None]:
    """allow = ALLOW|MERGE, review = REQUIRE_REVIEW|DELAY, block = BLOCK over contexts that have a decision.
    None (not 0) when there are no decided similar contexts."""
    decided = [c.decision for c in contexts if c.decision]
    n = len(decided)
    if n == 0:
        return {"allow": None, "review": None, "block": None, "n": 0}
    return {
        "allow": sum(d in ALLOW_DECISIONS for d in decided) / n,
        "review": sum(d in REVIEW_DECISIONS for d in decided) / n,
        "block": sum(d in BLOCK_DECISIONS for d in decided) / n,
        "n": n,
    }


def stats_by_decision(contexts: Sequence[SimilarContext]) -> list[DecisionStats]:
    """Which control decisions historically worked in similar contexts (question 7). Only measured outcomes count."""
    counts: Counter[str] = Counter()
    rewards: dict[str, list[float]] = {}
    for c in contexts:
        if not c.decision:
            continue
        counts[c.decision] += 1
        if c.reward is not None:
            rewards.setdefault(c.decision, []).append(c.reward)
    out = []
    for d in sorted(counts):
        rs = rewards.get(d, [])
        out.append(DecisionStats(
            decision=d, n=counts[d], with_outcome=len(rs),
            mean_reward=(sum(rs) / len(rs)) if rs else None,
            positive_rate=(sum(r > 0 for r in rs) / len(rs)) if rs else None,
        ))
    return out
