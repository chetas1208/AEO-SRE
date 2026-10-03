"""Feature-only rule scorer: the EvidenceRanker fallback when no trained artifact / model deps are present.

Pure python. Deliberately simple and *not* calibrated; callers must treat its output as `degraded`.
"""

from __future__ import annotations

import math

HEURISTIC_VERSION = "heuristic-rules-v1"
CLASS_ORDER = ("support", "contradiction", "insufficient")


def _v(f: dict[str, float], k: str, default: float = 0.0) -> float:
    x = f.get(k, float("nan"))
    return default if x != x else x


def heuristic_class_probs(f: dict[str, float]) -> tuple[float, float, float]:
    cov = _v(f, "claim_coverage")
    ent = _v(f, "ent_overlap", 0.5)
    exact = _v(f, "num_exact_frac", 1.0) if f.get("num_claim_count", 0) else 1.0
    mismatch = (
        1.6 * (f.get("num_conflict", 0) > 0) + 1.5 * (f.get("comp_violated", 0) > 0)
        + 1.3 * (f.get("year_conflict", 0) > 0) + 0.8 * (f.get("month_conflict", 0) > 0)
        + 1.0 * (f.get("neg_mismatch", 0) > 0) + 0.8 * min(f.get("antonym_conflict", 0), 2)
    )
    support = 2.6 * cov + 1.0 * _v(f, "bigram_coverage") + 0.8 * ent + 0.7 * (f.get("comp_satisfied", 0) > 0) \
        + 0.6 * (exact >= 0.99) - 1.2 * mismatch
    contradiction = 0.4 + 1.1 * mismatch + 0.8 * cov * (mismatch > 0) - 0.9 * (exact >= 0.99 and mismatch == 0)
    insufficient = 0.2 + 2.2 * (1 - cov) + 0.9 * (ent < 0.5) - 0.6 * mismatch
    z = [support, contradiction, insufficient]
    m = max(z)
    e = [math.exp(x - m) for x in z]
    s = sum(e)
    return e[0] / s, e[1] / s, e[2] / s
