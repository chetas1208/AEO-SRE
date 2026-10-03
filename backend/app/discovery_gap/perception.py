"""Profound perception adapter: what AI engines say about the brand, as normalized `PerceivedClaim`s.

Built ONLY from real Profound responses (or, in tests, payloads the caller injects and labels via `source_mode`).
Primary source: FactCheck claims (`POST /v2/reports/factcheck/claims`). Fallback (labelled lower confidence): sentences
of raw answer text (`POST /v2/prompts/answers`) that mention the brand and that G2's deterministic extractor can read.
Nothing is ever fabricated: an unavailable endpoint yields an explicit state and reason, never claims.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlparse

from app.changeguard.claims import extract_claims, normalize_text
from app.connectors.profound.errors import (
    ProfoundAuthError,
    ProfoundError,
    ProfoundNotConfigured,
    ProfoundNotFound,
    ProfoundPermissionError,
    ProfoundRateLimited,
    ProfoundValidationError,
)
from app.connectors.profound.requests import AnswersQuery, FactCheckClaimsQuery
from app.connectors.profound.timeutil import et_midnight_utc, last_complete_day

ORIGIN_FACTCHECK = "factcheck_claims"
ORIGIN_ANSWER = "answer_text"
ANSWER_CONFIDENCE_CAP = 0.5  # answer-text extraction is a lower-confidence perception than FactCheck
MAX_ANSWER_CLAIMS = 200
SOURCE_MODES = ("LIVE", "SIMULATED", "TEST")


def claim_key(text: str) -> str:
    return hashlib.sha256(normalize_text(text).encode()).hexdigest()[:16]


@dataclass
class PerceivedClaim:
    text: str
    origin: str  # factcheck_claims | answer_text
    engines: list[str] = field(default_factory=list)  # Profound model / platform names
    prompt: str | None = None
    topic: str | None = None
    occurrence: int = 1
    citation_sources: list[dict[str, str]] = field(default_factory=list)  # [{domain, url?}]
    reasoning: str | None = None
    evidence: Any = None
    observed_at: datetime | None = None
    window: tuple[str, str] | None = None
    source_mode: str = "LIVE"
    confidence_cap: float | None = None  # set for answer_text
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def key(self) -> str:
        return claim_key(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {"text": self.text, "origin": self.origin, "engines": self.engines, "prompt": self.prompt,
                "topic": self.topic, "occurrence": self.occurrence, "citation_sources": self.citation_sources,
                "reasoning": self.reasoning, "evidence": self.evidence,
                "observed_at": self.observed_at.isoformat() if self.observed_at else None,
                "window": list(self.window) if self.window else None, "source_mode": self.source_mode,
                "confidence_cap": self.confidence_cap, **({"extra": self.extra} if self.extra else {})}


@dataclass
class PerceptionResult:
    claims: list[PerceivedClaim] = field(default_factory=list)
    source: str | None = None  # factcheck_claims | answer_text | None
    factcheck_state: str = "not_run"  # ok | empty | unavailable | not_run
    factcheck_reason: str | None = None
    answers_state: str = "not_run"  # ok | empty | unavailable | not_run
    answers_reason: str | None = None
    requests_made: int = 0
    window: tuple[str, str] | None = None
    source_mode: str = "LIVE"
    fatal: str | None = None  # profound_not_configured | rate_limited ... (nothing usable at all)

    def as_dict(self) -> dict[str, Any]:
        return {"source": self.source, "claims": len(self.claims), "factcheck_state": self.factcheck_state,
                "factcheck_reason": self.factcheck_reason, "answers_state": self.answers_state,
                "answers_reason": self.answers_reason, "requests_made": self.requests_made,
                "window": list(self.window) if self.window else None, "source_mode": self.source_mode,
                "fatal": self.fatal}


# --------------------------------------------------------------------------- pure normalization
def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        for k in ("data", "rows", "claims", "answers"):
            v = payload.get(k)
            if isinstance(v, list):
                return [r for r in v if isinstance(r, dict)]
    return []


def _name(v: Any) -> str | None:
    if v is None or v == "":
        return None
    if isinstance(v, dict):
        v = v.get("name") or v.get("id")
    s = str(v).strip() if v is not None else ""
    return s or None


def _names(*values: Any) -> list[str]:
    out: list[str] = []
    for v in values:
        for x in (v if isinstance(v, (list, tuple)) else [v]):
            n = _name(x)
            if n and n not in out:
                out.append(n)
    return out


def domain_from(value: str) -> str:
    v = (value or "").strip()
    host = urlparse(v if "://" in v else f"//{v}").netloc or v.split("/")[0]
    return host.lower().removeprefix("www.")


def _sources(raw: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in (raw if isinstance(raw, (list, tuple)) else [raw]):
        if not item:
            continue
        if isinstance(item, dict):
            url = str(item.get("url") or item.get("page") or "").strip()
            dom = domain_from(str(item.get("domain") or url))
        else:
            url = str(item).strip() if "/" in str(item) else ""
            dom = domain_from(str(item))
        if not dom or "." not in dom:
            continue
        key = url or dom
        if key in seen:
            continue
        seen.add(key)
        out.append({"domain": dom, **({"url": url} if url else {})})
    return out


def _occurrence(v: Any) -> int:
    try:
        n = int(float(v))
    except (TypeError, ValueError):
        return 1
    return max(n, 1)


def normalize_factcheck_claims(payload: Any, *, window: tuple[str, str] | None = None, source_mode: str = "LIVE",
                               observed_at: datetime | None = None) -> list[PerceivedClaim]:
    """FactCheck claims items [claim, occurrence, reasoning, evidence, citation_sources, model(s), prompt, topic]."""
    at = observed_at or (et_midnight_utc(window[1]) if window else None)
    out: list[PerceivedClaim] = []
    for row in _rows(payload):
        text = str(row.get("claim") or "").strip()
        if len(text.split()) < 2:
            continue
        out.append(PerceivedClaim(
            text=text, origin=ORIGIN_FACTCHECK, engines=_names(row.get("models"), row.get("model")),
            prompt=_name(row.get("prompt")), topic=_name(row.get("topic")),
            occurrence=_occurrence(row.get("occurrence")), citation_sources=_sources(row.get("citation_sources")),
            reasoning=str(row["reasoning"]) if row.get("reasoning") else None, evidence=row.get("evidence"),
            observed_at=at, window=window, source_mode=source_mode,
            extra={k: row[k] for k in ("accuracy", "accurate", "inaccurate", "total_claims", "cluster_id")
                   if row.get(k) is not None},
        ))
    return out


_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_BULLET = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")


def _clean_lines(text: str) -> list[str]:
    """Strip markdown noise (links, emphasis, bullets, headings) so the extractor sees plain sentences."""
    out = []
    for line in text.splitlines():
        line = _LINK.sub(r"\1", line)
        line = _BULLET.sub("", line)
        line = re.sub(r"[*_`#>]+", "", line).strip()
        if len(line.split()) >= 3:
            out.append(line)
    return out


def normalize_answer_claims(payload: Any, *, brand_terms: list[str], window: tuple[str, str] | None = None,
                            source_mode: str = "LIVE") -> list[PerceivedClaim]:
    """Sentences of answer text that name the brand AND that the deterministic extractor can read (known feature)."""
    terms = [t.strip().lower() for t in brand_terms if t and t.strip()]
    out: list[PerceivedClaim] = []
    if not terms:  # without a brand anchor we cannot tell claims about us from claims about anyone else
        return out
    for row in _rows(payload):
        text = row.get("response")
        if not isinstance(text, str) or not text.strip():
            continue
        at = None
        try:
            at = et_midnight_utc(str(row.get("date"))) if row.get("date") else (et_midnight_utc(window[1]) if window else None)
        except ValueError:
            at = None
        cites = _sources(row.get("citations") or row.get("citation_details"))
        for c in (c for line in _clean_lines(text) for c in extract_claims(line)):
            low = c.text.lower()
            if c.subject is None or not any(t in low for t in terms):
                continue
            out.append(PerceivedClaim(
                text=c.text, origin=ORIGIN_ANSWER, engines=_names(row.get("model")), prompt=_name(row.get("prompt")),
                topic=_name(row.get("topic")), occurrence=1, citation_sources=cites, observed_at=at, window=window,
                source_mode=source_mode, confidence_cap=ANSWER_CONFIDENCE_CAP,
                extra={"run_id": row.get("run_id")} if row.get("run_id") else {}))
            if len(out) >= MAX_ANSWER_CLAIMS:
                return out
    return out


# --------------------------------------------------------------------------- fetching
def _unavailable_reason(exc: ProfoundError) -> str:
    base = type(exc).__name__.removeprefix("Profound")
    detail = exc.detail if isinstance(exc.detail, (str, dict, list)) else None
    msg = f"{base}: {exc.message}"
    if exc.status:
        msg += f" (HTTP {exc.status})"
    if detail:
        msg += f" detail={str(detail)[:300]}"
    return msg


async def fetch_perception(client: Any, category_id: str, *, brand_terms: list[str] | None = None,
                           now: datetime | None = None, window_days: int = 14, factcheck_pages: int = 2,
                           answers_fallback: bool = True, answers_limit: int = 50, source_mode: str = "LIVE"
                           ) -> PerceptionResult:
    """Small bounded fetch: <= `factcheck_pages` + 1 requests. `client` is a ProfoundClient (or a test double).
    Callers must pass source_mode LIVE only when `client` talks to the real API."""
    now = now or datetime.now(UTC)
    end = last_complete_day(now)
    window = ((end - timedelta(days=window_days - 1)).isoformat(), end.isoformat())
    res = PerceptionResult(window=window, source_mode=source_mode)

    # 1. FactCheck claims
    claims: list[PerceivedClaim] = []
    cursor: str | None = None
    try:
        for _ in range(max(1, factcheck_pages)):
            q = FactCheckClaimsQuery(category_id=category_id, start_date=window[0], end_date=window[1],
                                     include=["reasoning", "models", "evidence", "citation_sources"],
                                     limit=100, cursor=cursor)
            resp = await client.factcheck_claims(q)
            res.requests_made += 1
            claims += normalize_factcheck_claims(resp.data, window=window, source_mode=source_mode)
            cursor = getattr(resp, "next_cursor", None)
            if not cursor:
                break
    except ProfoundNotConfigured as exc:
        res.factcheck_state, res.factcheck_reason = "unavailable", _unavailable_reason(exc)
        res.fatal = "profound_not_configured"
        return res
    except ProfoundRateLimited as exc:
        res.requests_made += 1
        res.factcheck_state, res.factcheck_reason = "unavailable", _unavailable_reason(exc)
        res.fatal = "rate_limited"
        return res
    except (ProfoundPermissionError, ProfoundNotFound, ProfoundValidationError, ProfoundAuthError, ProfoundError) as exc:
        res.requests_made += 1
        res.factcheck_state, res.factcheck_reason = "unavailable", _unavailable_reason(exc)
    else:
        if claims:
            res.factcheck_state, res.claims, res.source = "ok", claims, ORIGIN_FACTCHECK
            return res
        res.factcheck_state = "empty"
        res.factcheck_reason = "FactCheck responded but returned no claims for the window (set up, nothing flagged yet)"

    # 2. Answer-text fallback (lower confidence), only when asked and when we have a brand anchor
    if not answers_fallback:
        return res
    if not brand_terms:
        res.answers_state, res.answers_reason = "unavailable", "no brand terms to anchor answer-text claims"
        return res
    try:
        q2 = AnswersQuery(category_id=category_id, start_date=window[0], end_date=window[1],
                          include=["date", "model", "prompt", "topic", "response", "citations", "run_id"],
                          limit=min(max(answers_limit, 1), 200))
        resp2 = await client.answers(q2)
        res.requests_made += 1
    except ProfoundError as exc:
        res.requests_made += 1
        res.answers_state, res.answers_reason = "unavailable", _unavailable_reason(exc)
        return res
    got = normalize_answer_claims(resp2.data, brand_terms=brand_terms, window=window, source_mode=source_mode)
    if got:
        res.answers_state, res.claims, res.source = "ok", got, ORIGIN_ANSWER
    else:
        res.answers_state = "empty"
        res.answers_reason = "answers endpoint healthy but no brand-mentioning claim the extractor could read"
    return res
