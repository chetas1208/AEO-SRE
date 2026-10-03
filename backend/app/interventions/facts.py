"""Evidence normalisation and grounding: patch content may only use claims present in supplied evidence.

A `Fact` is a verbatim sentence taken from an evidence excerpt (or an operator-supplied canonical fact). Content
generators (template or LLM) may only assert Facts; `ungrounded_tokens` / `check_claims` reject anything else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.domain.enums import EvidenceStatus, EvidenceType

_ASSERTABLE_STATUS = {EvidenceStatus.LIVE.value, EvidenceStatus.CHANGED.value}
_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])|\n+")
_NUMBER = re.compile(r"\d[\d,.]*\d|\d")
_URL = re.compile(r"https?://[^\s)>\"']+")
_ACRONYM = re.compile(r"\b[A-Z][A-Z0-9]{1,}[A-Za-z0-9]*\b")
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9\-']+")
_STOP = frozenset(
    ["the", "and", "for", "with", "that", "this", "from", "are", "was", "were", "has", "have", "had", "not", "but", "you", "your", "our", "their", "its", "can", "will", "may", "all", "any", "into", "than", "then", "also", "such", "via", "per", "each", "more", "most", "other", "which", "who", "how", "what", "when", "where"]
)
_GENERIC_ACRONYMS = frozenset({"faq", "faqs", "url", "api", "seo", "aeo", "html", "json", "pdf", "q&a", "ai"})
MIN_SENTENCE = 25
MAX_SENTENCE = 400


@dataclass(frozen=True)
class EvidenceItem:
    id: str
    type: str
    status: str
    title: str = ""
    url: str | None = None
    excerpt: str = ""
    source: str | None = None
    retrieved_at: datetime | None = None
    confidence: float | None = None
    content_hash: str | None = None

    @classmethod
    def from_any(cls, obj: Any) -> EvidenceItem:
        def g(name: str, default: Any = None) -> Any:
            return obj.get(name, default) if isinstance(obj, dict) else getattr(obj, name, default)

        def s(v: Any) -> Any:
            return getattr(v, "value", v)

        return cls(
            id=str(g("id", "")), type=str(s(g("type", EvidenceType.EXTERNAL.value))),
            status=str(s(g("status", EvidenceStatus.LIVE.value))), title=g("title", "") or "",
            url=g("url"), excerpt=g("excerpt", "") or "", source=g("source"),
            retrieved_at=g("retrieved_at"), confidence=g("confidence"), content_hash=g("content_hash"),
        )

    @property
    def assertable(self) -> bool:
        return self.status in _ASSERTABLE_STATUS and self.type != EvidenceType.INFERENCE.value


@dataclass(frozen=True)
class Fact:
    id: str
    text: str
    kind: str  # owned | competitor | external | canonical
    url: str | None = None
    evidence_id: str | None = None
    retrieved_at: datetime | None = None


@dataclass
class FactBase:
    facts: list[Fact] = field(default_factory=list)

    def of(self, *kinds: str) -> list[Fact]:
        return [f for f in self.facts if f.kind in kinds]

    def by_id(self) -> dict[str, Fact]:
        return {f.id: f for f in self.facts}

    @property
    def corpus(self) -> str:
        return "\n".join(f.text for f in self.facts)


def split_sentences(text: str) -> list[str]:
    out: list[str] = []
    for raw in _SENT_SPLIT.split(text or ""):
        s = " ".join(raw.split()).lstrip("#*->• ").strip()
        if MIN_SENTENCE <= len(s) <= MAX_SENTENCE and not s.endswith("?"):
            out.append(s)
    return out


def _kind_for(item: EvidenceItem) -> str | None:
    if item.type == EvidenceType.OWNED.value:
        return "owned"
    if item.type == EvidenceType.COMPETITOR.value:
        return "competitor"
    if item.type == EvidenceType.EXTERNAL.value:
        return "external"
    return None  # profound metrics / inference rows are context, never assertable content


def build_fact_base(
    evidence: list[EvidenceItem], canonical_facts: list[Any] | None = None, *, per_item: int = 4, cap: int = 16
) -> FactBase:
    facts: list[Fact] = []
    for item in evidence:
        kind = _kind_for(item)
        if kind is None or not item.assertable:
            continue
        for i, sent in enumerate(split_sentences(item.excerpt)[:per_item]):
            facts.append(Fact(f"{item.id}#{i}", sent, kind, item.url, item.id, item.retrieved_at))
    for j, cf in enumerate(canonical_facts or []):
        text, url = (cf.get("text"), cf.get("source_url")) if isinstance(cf, dict) else (str(cf), None)
        if text and text.strip():
            facts.append(Fact(f"canonical#{j}", " ".join(text.split()), "canonical", url))
    return FactBase(facts[:cap] if len(facts) <= cap else _balanced(facts, cap))


def _balanced(facts: list[Fact], cap: int) -> list[Fact]:
    """Keep canonical + owned facts first, then others, up to cap."""
    order = {"canonical": 0, "owned": 1, "competitor": 2, "external": 3}
    return sorted(facts, key=lambda f: order.get(f.kind, 9))[:cap]


def _norm_num(tok: str) -> str:
    return tok.replace(",", "").rstrip(".")


def content_tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text) if len(w) > 3 and w.lower() not in _STOP}


def ungrounded_tokens(text: str, corpus: str) -> list[str]:
    """Numbers, URLs and acronyms in `text` that never occur in `corpus` (strong signal of invented facts)."""
    corpus_l = corpus.lower()
    corpus_nums = {_norm_num(n) for n in _NUMBER.findall(corpus)}
    bad: list[str] = []
    for url in _URL.findall(text):
        if url.rstrip(".,;").lower() not in corpus_l:
            bad.append(url)
    for num in _NUMBER.findall(_URL.sub(" ", text)):
        if _norm_num(num) not in corpus_nums:
            bad.append(num)
    for acr in _ACRONYM.findall(_URL.sub(" ", text)):
        if acr.lower() not in corpus_l and acr.lower() not in _GENERIC_ACRONYMS:
            bad.append(acr)
    return sorted(set(bad))


def claim_supported(claim: str, facts: list[Fact], *, threshold: float = 0.6) -> bool:
    if not facts:
        return False
    need = content_tokens(claim)
    if not need:
        return True
    have: set[str] = set()
    for f in facts:
        have |= content_tokens(f.text)
    return len(need & have) / len(need) >= threshold and not ungrounded_tokens(claim, "\n".join(f.text for f in facts))


def check_claims(claims: list[dict[str, Any]], fb: FactBase) -> list[str]:
    """Problems with LLM-supplied claims: each must cite known fact ids and be supported by them."""
    problems: list[str] = []
    index = fb.by_id()
    if not claims:
        problems.append("no claims supplied")
    for c in claims:
        cited = [index[i] for i in c.get("fact_ids", []) if i in index]
        if not cited:
            problems.append(f"claim cites no known fact: {str(c.get('text'))[:80]!r}")
        elif not claim_supported(str(c.get("text", "")), cited):
            problems.append(f"claim not supported by cited facts: {str(c.get('text'))[:80]!r}")
    return problems
