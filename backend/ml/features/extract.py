"""EvidenceRanker feature extraction: pure functions, no I/O, no numpy/torch required.

`extract_features(claim, passage, meta, cosine)` returns a flat dict keyed by `FEATURE_NAMES`.
Missing information is `nan` (LightGBM treats it as missing; the heuristic fallback checks `isnan`).
"""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlparse

from ml.features import text as T

NAN = float("nan")

LEXICAL_FEATURES: tuple[str, ...] = (
    "claim_len_log", "passage_len_log", "len_ratio",
    "tok_jaccard", "claim_coverage", "passage_coverage", "bigram_coverage", "unmatched_claim_tokens",
    "ent_claim_count", "ent_overlap", "ent_jaccard", "ent_passage_extra",
    "num_claim_count", "num_passage_count", "num_exact_frac", "num_conflict", "num_min_rel_diff",
    "num_unmatched_claim", "comp_present", "comp_satisfied", "comp_violated", "approx_comparator",
    "year_claim_count", "year_overlap_frac", "year_conflict", "month_conflict",
    "neg_claim", "neg_passage", "neg_mismatch", "antonym_conflict", "antonym_shared",
    "title_in_claim", "title_sim",
    "temporal_cue_claim", "temporal_cue_passage", "time_sensitivity",
)
# Context features that exist at inference time (web evidence) but have no counterpart in the public
# training corpora; they feed the freshness prior in `app.evidence.ranker`, not the trained heads.
META_FEATURES: tuple[str, ...] = ("source_age_log", "is_owned", "revision_distance_log")
EMBEDDING_FEATURE = "cosine"
FEATURE_NAMES: tuple[str, ...] = (EMBEDDING_FEATURE, *LEXICAL_FEATURES, *META_FEATURES)


def _isnan(x: float) -> bool:
    return x != x


def _parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def source_age_days(meta: dict[str, Any], now: datetime | None = None) -> float:
    """Age in days of the source: explicit `age_days`/`source_age_days`, else now - (`published_at`|`last_modified`|`observed_at`)."""
    for key in ("age_days", "source_age_days"):
        if meta.get(key) is not None:
            try:
                return max(0.0, float(meta[key]))
            except (TypeError, ValueError):
                return NAN
    now = now or datetime.now(UTC)
    for key in ("published_at", "last_modified", "observed_at", "retrieved_at"):
        dt = _parse_dt(meta.get(key))
        if dt is not None:
            return max(0.0, (now - dt).total_seconds() / 86400.0)
    return NAN


def _host(url: str) -> str:
    host = (urlparse(url if "//" in url else f"//{url}").hostname or "").lower()
    return host[4:] if host.startswith("www.") else host


def is_owned(meta: dict[str, Any]) -> float:
    """1.0 owned, 0.0 third-party, nan unknown. Uses `owned` or `url` vs `owned_domains`."""
    if meta.get("owned") is not None:
        return 1.0 if bool(meta["owned"]) else 0.0
    url, domains = meta.get("url"), meta.get("owned_domains")
    if url and domains:
        host = _host(str(url))
        return 1.0 if any(host == _host(d) or host.endswith("." + _host(d)) for d in domains) else 0.0
    return NAN


def _numeric_features(claim: str, passage: str) -> dict[str, float]:
    cnums = T.extract_numbers(claim)
    pnums = T.extract_numbers(passage)
    out = {
        "num_claim_count": float(len(cnums)), "num_passage_count": float(len(pnums)),
        "num_exact_frac": NAN, "num_conflict": 0.0, "num_min_rel_diff": NAN, "num_unmatched_claim": 0.0,
        "comp_present": 0.0, "comp_satisfied": 0.0, "comp_violated": 0.0,
        "approx_comparator": float(T.has_approx_comparator(claim)),
    }
    if not cnums:
        return out
    matched, diffs = 0, []
    for c in cnums:
        rel = [abs(c - p) / max(abs(c), abs(p), 1e-9) for p in pnums]
        best = min(rel) if rel else 1.0
        diffs.append(best)
        if best <= 0.01:
            matched += 1
    out["num_exact_frac"] = matched / len(cnums)
    out["num_min_rel_diff"] = sum(diffs) / len(diffs)
    out["num_unmatched_claim"] = float(len(cnums) - matched)
    # a claim number is in "conflict" when it is absent but the passage has a number of similar magnitude
    conflict = 0
    for c, d in zip(cnums, diffs, strict=True):
        if d > 0.01 and any(0.05 <= (p / c if c else 0) <= 20 for p in pnums if p):
            conflict += 1
    out["num_conflict"] = float(conflict)
    sat = viol = present = 0
    for val, direction in T.numbers_with_comparators(claim):
        if direction == 0:
            continue
        present += 1
        comparable = [p for p in pnums if val and 0.05 <= p / val <= 20]
        if not comparable:
            continue
        ok = any((p > val) if direction > 0 else (p < val) for p in comparable)
        sat += int(ok)
        viol += int(not ok)
    out["comp_present"], out["comp_satisfied"], out["comp_violated"] = float(present), float(sat), float(viol)
    return out


def _antonym_features(ctoks: set[str], ptoks: set[str]) -> tuple[float, float]:
    conflict = shared = 0
    for a, b in T.ANTONYM_PAIRS:
        for x, y in ((a, b), (b, a)):
            if x in ctoks:
                if y in ptoks and x not in ptoks:
                    conflict += 1
                elif x in ptoks:
                    shared += 1
    return float(conflict), float(shared)


def extract_features(
    claim: str, passage: str, meta: dict[str, Any] | None = None, cosine: float | None = None
) -> dict[str, float]:
    meta = meta or {}
    claim_n, passage_n = T.normalize(claim), T.normalize(passage)
    ctoks, ptoks = T.content_tokens(claim_n), T.content_tokens(passage_n)
    cset, pset = set(ctoks), set(ptoks)
    f: dict[str, float] = {k: NAN for k in FEATURE_NAMES}

    f["cosine"] = NAN if cosine is None else float(cosine)
    f["claim_len_log"] = math.log1p(len(ctoks))
    f["passage_len_log"] = math.log1p(len(ptoks))
    f["len_ratio"] = len(ctoks) / max(len(ptoks), 1)
    f["tok_jaccard"] = T.jaccard(cset, pset)
    f["claim_coverage"] = len(cset & pset) / max(len(cset), 1)
    f["passage_coverage"] = len(cset & pset) / max(len(pset), 1)
    cb, pb = T.ngrams(ctoks, 2), T.ngrams(ptoks, 2)
    f["bigram_coverage"] = len(cb & pb) / max(len(cb), 1)
    f["unmatched_claim_tokens"] = float(len(cset - pset))

    cent, pent = T.entities(claim_n), T.entities(passage_n)
    plow = passage_n.lower()
    ent_hit = {e for e in cent if e in plow or all(w in pset or w in plow for w in e.split())}
    f["ent_claim_count"] = float(len(cent))
    f["ent_overlap"] = len(ent_hit) / len(cent) if cent else NAN
    f["ent_jaccard"] = T.jaccard(cent, pent)
    f["ent_passage_extra"] = float(len(pent - cent))

    f.update(_numeric_features(claim_n, passage_n))

    cy, py = T.extract_years(claim_n), T.extract_years(passage_n)
    f["year_claim_count"] = float(len(cy))
    f["year_overlap_frac"] = len(cy & py) / len(cy) if cy else NAN
    f["year_conflict"] = float(bool(cy) and bool(py) and not (cy & py))
    cm, pm = T.extract_months(claim_n), T.extract_months(passage_n)
    f["month_conflict"] = float(bool(cm) and bool(pm) and not (cm & pm))

    nc, npass = T.negation_count(claim_n), T.negation_count(passage_n)
    f["neg_claim"], f["neg_passage"] = float(nc), float(npass)
    f["neg_mismatch"] = float((nc > 0) != (npass > 0))
    raw_c, raw_p = set(T.tokens(claim_n)), set(T.tokens(passage_n))
    f["antonym_conflict"], f["antonym_shared"] = _antonym_features(raw_c, raw_p)

    title = T.normalize(str(meta.get("title") or ""))
    if title:
        ttoks = set(T.content_tokens(title))
        f["title_in_claim"] = float(title.lower() in claim_n.lower())
        f["title_sim"] = len(ttoks & cset) / max(len(ttoks), 1)

    tc = float(bool(raw_c & T.TEMPORAL_CUES))
    tp = float(bool(raw_p & T.TEMPORAL_CUES))
    f["temporal_cue_claim"], f["temporal_cue_passage"] = tc, tp
    has_vals = bool(T.extract_numbers(passage_n)) or bool(py)
    f["time_sensitivity"] = min(1.0, 0.35 * float(has_vals) + 0.35 * tp + 0.3 * tc)

    age = source_age_days(meta)
    f["source_age_log"] = NAN if _isnan(age) else math.log1p(age)
    f["is_owned"] = is_owned(meta)
    rd = meta.get("revision_distance")
    f["revision_distance_log"] = math.log1p(max(float(rd), 0.0)) if isinstance(rd, (int, float)) else NAN
    return f


def feature_vector(feats: dict[str, float], names: tuple[str, ...] | list[str]) -> list[float]:
    return [feats.get(n, NAN) for n in names]


_TITLE_CLEAN = re.compile(r"\s+")


def clean_title(title: str) -> str:
    return _TITLE_CLEAN.sub(" ", T.normalize(title))
