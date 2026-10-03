"""Historical memory: retrieve similar PAST experiments and aggregate them into numeric policy features.

Similarity = 0.6 * structured + 0.4 * lexical
  structured: same incident category (gate) x cosine over a fixed subset of the stored ctx vectors (matched BY NAME)
  lexical:    TF-IDF cosine over incident title/summary/root-cause text. This is bag-of-words similarity, NOT neural
              embeddings; the name says so on purpose.

Only experiments that finished with a measured outcome (an `ExperimentOutcome` row, or a legacy `Reward` row) count;
dry runs never do. The bandit receives ONLY the aggregates (`MemorySummary.features()`): counts, means, rates,
uncertainty. No raw text ever reaches the policy. Retrieval is "as of" a timestamp so a replay cannot see the future.
"""
from __future__ import annotations

import math
import re
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Incident
from app.models.evidence import Hypothesis
from app.models.interventions import Experiment, ExperimentOutcome, Reward

STRUCTURED_WEIGHT = 0.6
LEXICAL_WEIGHT = 0.4
MIN_SIMILARITY = 0.45
DEFAULT_K = 10
FAVORABLE_BAND = 0.05  # |reward| <= band is NEUTRAL when an outcome label is missing (legacy rewards)

# Context features compared structurally (names exist in every ctx schema).
STRUCTURAL_FEATURES: tuple[str, ...] = (
    "visibility_delta", "citation_delta", "accuracy_delta", "competitor_delta", "prompt_volume", "buyer_intent",
    "owned_source", "third_party_source", "content_exists", "factual_conflict",
)
_TOKEN = re.compile(r"[a-z0-9]{3,}")
_STOP = frozenset("the and for with that this from are was were has have had not but all any can will our their".split())


def tokens(text: str) -> list[str]:
    return [t for t in _TOKEN.findall((text or "").lower()) if t not in _STOP]


def _tfidf(docs: list[list[str]]) -> list[dict[str, float]]:
    df: Counter[str] = Counter()
    for d in docs:
        df.update(set(d))
    n = len(docs)
    out = []
    for d in docs:
        tf = Counter(d)
        out.append({t: (1 + math.log(c)) * (math.log((1 + n) / (1 + df[t])) + 1.0) for t, c in tf.items()})
    return out


def _cos(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(v * b.get(k, 0.0) for k, v in a.items())
    na, nb = math.sqrt(sum(v * v for v in a.values())), math.sqrt(sum(v * v for v in b.values()))
    return dot / (na * nb) if na and nb else 0.0


def lexical_similarities(query: str, docs: list[str]) -> list[float]:
    vecs = _tfidf([tokens(query), *[tokens(d) for d in docs]])
    return [_cos(vecs[0], v) for v in vecs[1:]]


def structured_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    """1 - normalised L1 distance over the structural features (all in [-1, 1] / [0, 1])."""
    if not a or not b:
        return 0.0
    d = sum(abs(a.get(k, 0.0) - b.get(k, 0.0)) for k in STRUCTURAL_FEATURES) / (2.0 * len(STRUCTURAL_FEATURES))
    return max(0.0, 1.0 - d)


def outcome_from_reward(total: float) -> str:
    return "favorable" if total > FAVORABLE_BAND else "unfavorable" if total < -FAVORABLE_BAND else "neutral"


@dataclass(frozen=True)
class SimilarExperiment:
    experiment_id: uuid.UUID
    action: str
    outcome: str
    reward: float | None
    confidence: str
    verification_delay_hours: float | None
    similarity: float
    structured: float
    lexical: float

    def to_json(self) -> dict[str, Any]:
        return {"experiment_id": str(self.experiment_id), "action": self.action, "outcome": self.outcome,
                "reward": self.reward, "confidence": self.confidence,
                "verification_delay_hours": self.verification_delay_hours, "similarity": round(self.similarity, 4),
                "structured": round(self.structured, 4), "lexical": round(self.lexical, 4)}


@dataclass
class MemorySummary:
    similar_n: int = 0
    mean_reward: float = 0.0
    favorable_rate: float = 0.5
    uncertainty: float = 1.0
    rows: list[SimilarExperiment] = field(default_factory=list)
    method: str = "structured+lexical(tf-idf) similarity; no neural embeddings"

    def features(self) -> dict[str, float]:
        """Numbers only: this dict is what the bandit sees."""
        return {"memory_similar_n": float(self.similar_n), "memory_mean_reward": self.mean_reward,
                "memory_favorable_rate": self.favorable_rate, "memory_uncertainty": self.uncertainty,
                "historical_success": self.favorable_rate}

    def to_json(self) -> dict[str, Any]:
        return {"similar_n": self.similar_n, "mean_reward": self.mean_reward, "favorable_rate": self.favorable_rate,
                "uncertainty": self.uncertainty, "method": self.method, "rows": [r.to_json() for r in self.rows]}


def aggregate(rows: list[SimilarExperiment]) -> MemorySummary:
    """Similarity-weighted aggregates. Inconclusive outcomes count toward n but not toward reward / favorable rate."""
    if not rows:
        return MemorySummary()
    rewarded = [r for r in rows if r.reward is not None]
    decided = [r for r in rows if r.outcome in ("favorable", "unfavorable", "neutral")]
    wsum = sum(r.similarity for r in rewarded)
    mean = sum(r.similarity * r.reward for r in rewarded) / wsum if wsum else 0.0  # type: ignore[operator]
    fav = (sum(1 for r in decided if r.outcome == "favorable") + 1.0) / (len(decided) + 2.0)  # Laplace
    return MemorySummary(similar_n=len(rows), mean_reward=float(max(-1.0, min(1.0, mean))), favorable_rate=fav,
                         uncertainty=1.0 / math.sqrt(len(decided) + 1.0), rows=rows)


def _ctx_by_name(cv: Any) -> dict[str, float]:
    if isinstance(cv, dict) and "names" in cv and "values" in cv:
        return {n: float(v) for n, v in zip(cv["names"], cv["values"], strict=False)}
    return {}


async def _incident_text(session: AsyncSession, incident: Incident) -> str:
    hyps = (await session.execute(select(Hypothesis.title).where(Hypothesis.incident_id == incident.id))).scalars().all()
    return " ".join([incident.title or "", incident.summary or "", *hyps])


async def retrieve_similar(
    session: AsyncSession, incident: Incident, context: Any, *, k: int = DEFAULT_K,
    as_of: datetime | None = None, min_similarity: float = MIN_SIMILARITY,
) -> MemorySummary:
    """Similar finished experiments (excluding this incident's own), best first, plus the aggregates."""
    query_ctx = _ctx_by_name(context) if isinstance(context, dict) else {}
    if not query_ctx and context is not None:
        from app.policy.features import FEATURE_NAMES, coerce_context

        query_ctx = dict(zip(FEATURE_NAMES, (float(v) for v in coerce_context(context)), strict=True))
    exps = (await session.execute(
        select(Experiment).where(Experiment.incident_id != incident.id, Experiment.dry_run.is_(False))
    )).scalars().all()
    if as_of is not None:
        exps = [e for e in exps if e.created_at is None or _aware(e.created_at) <= _aware(as_of)]
    outcomes = {o.experiment_id: o for o in (await session.execute(select(ExperimentOutcome))).scalars()}
    rewards = {r.experiment_id: r for r in (await session.execute(select(Reward))).scalars()}
    cands = [e for e in exps if e.id in outcomes or e.id in rewards]
    if not cands:
        return MemorySummary()
    inc_ids = {e.incident_id for e in cands}
    incidents = {i.id: i for i in (await session.execute(select(Incident).where(Incident.id.in_(inc_ids)))).scalars()}
    texts = {iid: await _incident_text(session, inc) for iid, inc in incidents.items()}
    lex = lexical_similarities(await _incident_text(session, incident), [texts.get(e.incident_id, "") for e in cands])
    scored: list[SimilarExperiment] = []
    for e, lx in zip(cands, lex, strict=True):
        inc = incidents.get(e.incident_id)
        if inc is None or inc.category != incident.category:
            continue
        st = structured_similarity(query_ctx, _ctx_by_name(e.context_vector))
        sim = STRUCTURED_WEIGHT * st + LEXICAL_WEIGHT * lx
        if sim < min_similarity:
            continue
        o, r = outcomes.get(e.id), rewards.get(e.id)
        reward = o.reward_total if o is not None else (r.total if r is not None else None)
        label = o.outcome if o is not None else outcome_from_reward(r.total)  # type: ignore[union-attr]
        delay = None
        if e.executed_at is not None:
            seen = (o.observed_at if o is not None else None) or e.evaluated_at
            if seen is not None:
                delay = round((_aware(seen) - _aware(e.executed_at)).total_seconds() / 3600.0, 2)
        scored.append(SimilarExperiment(e.id, str(e.selected_action), label, reward,
                                        o.causal_confidence if o is not None else "low", delay, sim, st, lx))
    scored.sort(key=lambda s: (-s.similarity, str(s.experiment_id)))
    return aggregate(scored[:k])


def _aware(dt: datetime) -> datetime:
    from datetime import UTC

    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
