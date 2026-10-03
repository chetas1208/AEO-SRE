"""Public-web evidence collection for an incident (A4).

collect_evidence(session, incident, emit) fetches own / canonical / competitor / cited pages, picks the blocks
that overlap the incident topic and prompts, and builds Evidence rows. Failed fetches become `failed` /
`unavailable` evidence with no content; nothing is inferred about a page that could not be read.
"""

from __future__ import annotations

import asyncio
import inspect
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import structlog

from app.connectors.web import WebCollector, diff_snapshots, host_matches, registrable_host
from app.connectors.web.source import classify_source
from app.connectors.web.types import FetchResult
from app.domain.enums import EvidenceStatus, EvidenceType, StepStatus

log = structlog.get_logger()

Emit = Callable[[str, StepStatus | str, str, dict | None], Any | Awaitable[Any]]
STALE_AFTER_DAYS = 365
MIN_BLOCK_RELEVANCE = 0.15
MAX_BLOCKS_PER_PAGE = 3
EXCERPT_CHARS = 600
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9\-\+\.]{1,}")
_STOP = frozenset(
    """the and for with that this from are was were has have had not but you your our their its can will what
    which who how why when where does did into than then them they about over under more most some any all out
    use used using best top vs versus compare comparison between should would could may might been being also
    one two per via get got make makes made need needs like just very new""".split()
)


@dataclass(slots=True)
class EvidenceDraft:
    """Stand-in used only when app.models.evidence is not importable. Same attribute names as Evidence."""

    type: str
    status: str
    title: str
    source: str
    url: str
    content_hash: str | None
    excerpt: str
    retrieval_method: str
    retrieved_at: datetime
    observed_at: datetime | None
    support_score: float | None = None
    contradiction_score: float | None = None
    insufficient_score: float | None = None
    freshness_risk: float | None = None
    confidence: float | None = None
    raw: dict = field(default_factory=dict)
    incident_id: Any = None


@dataclass(slots=True)
class IncidentContext:
    org_name: str = ""
    own_domain: str = ""
    competitor_domains: list[str] = field(default_factory=list)
    canonical_domains: list[str] = field(default_factory=list)
    topic: str = ""
    prompts: list[str] = field(default_factory=list)
    cited_urls: list[str] = field(default_factory=list)
    claim: str = ""
    keywords: list[str] = field(default_factory=list)


def classify_domain(
    host_or_url: str, own: str, competitors: list[str] | None = None, canonical: list[str] | None = None
) -> EvidenceType:
    host = registrable_host(host_or_url)
    if own and host_matches(host, own):
        return EvidenceType.OWNED
    if any(host_matches(host, c) for c in canonical or []):
        return EvidenceType.OWNED
    if any(host_matches(host, c) for c in competitors or []):
        return EvidenceType.COMPETITOR
    return EvidenceType.EXTERNAL


# ---- relevance ---------------------------------------------------------------------------------


def _tokens(text: str) -> list[str]:
    return [t.lower().strip(".-") for t in _TOKEN.findall(text or "") if t.lower() not in _STOP]


def build_query_terms(ctx: IncidentContext) -> dict[str, float]:
    """Weighted term set: topic 3, entities (acronyms/digits) 3, title/summary 2, prompts 1, brand 1."""
    weights: dict[str, float] = {}

    def add(text: str, w: float) -> None:
        for tok in _tokens(text):
            if len(tok) < 3 and not tok.isdigit():
                continue
            weights[tok] = max(weights.get(tok, 0.0), w)

    add(ctx.org_name, 1.0)
    for p in ctx.prompts[:25]:
        add(p, 1.0)
    add(ctx.claim, 2.0)
    add(ctx.topic, 3.0)
    for k in ctx.keywords:
        add(k, 2.5)
    for raw in _TOKEN.findall(f"{ctx.claim} {ctx.topic}"):
        if raw.isupper() and len(raw) >= 2 or any(c.isdigit() for c in raw):
            weights[raw.lower().strip(".-")] = 3.0
    return weights


def score_text(text: str, terms: dict[str, float], topic: str = "") -> tuple[float, list[str]]:
    """Weighted keyword/entity overlap in [0,1] plus matched terms. Deterministic, no model."""
    if not terms or not text:
        return 0.0, []
    present = set(_tokens(text))
    matched = sorted(t for t in terms if t in present)
    top = sorted(terms.values(), reverse=True)[:12]
    denom = sum(top) or 1.0
    score = min(1.0, sum(sorted((terms[t] for t in matched), reverse=True)[:12]) / denom)
    if topic and topic.lower() in text.lower():
        score = min(1.0, score + 0.25)
    return round(score, 4), matched


def pick_relevant_blocks(result: FetchResult, terms: dict[str, float], topic: str) -> list[dict[str, Any]]:
    scored = []
    for b in result.blocks:
        text = f"{b.heading}. {b.text}" if b.heading else b.text
        s, matched = score_text(text, terms, topic)
        if b.heading:
            hs, _ = score_text(b.heading, terms, topic)
            s = min(1.0, s + 0.1 * hs)
        if s >= MIN_BLOCK_RELEVANCE:
            scored.append(
                {
                    "heading": b.heading,
                    "text": b.text,
                    "hash": b.hash,
                    "index": b.index,
                    "relevance": round(s, 4),
                    "matched_terms": matched[:15],
                }
            )
    scored.sort(key=lambda x: -x["relevance"])
    return scored[:MAX_BLOCKS_PER_PAGE]


# ---- context resolution (duck-typed against the models contract) -------------------------------


def _urls_in(obj: Any, out: list[str], depth: int = 0) -> None:
    if depth > 5:
        return
    if isinstance(obj, str):
        if obj.startswith(("http://", "https://")):
            out.append(obj)
    elif isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, str) and k.lower() in ("url", "source_url", "cited_url", "link", "href"):
                if v.startswith(("http://", "https://")):
                    out.append(v)
            else:
                _urls_in(v, out, depth + 1)
    elif isinstance(obj, list | tuple):
        for v in obj:
            _urls_in(v, out, depth + 1)


async def resolve_context(session: Any, incident: Any) -> IncidentContext:
    ctx_json = getattr(incident, "context", None) or {}
    org = getattr(incident, "organization", None)
    cluster = getattr(incident, "prompt_cluster", None)
    sig_urls: list[str] = []
    try:
        if org is None and session is not None and getattr(incident, "org_id", None) is not None:
            from app.models.core import Organization

            org = await session.get(Organization, incident.org_id)
        if (
            cluster is None
            and session is not None
            and getattr(incident, "prompt_cluster_id", None) is not None
        ):
            from app.models.core import PromptCluster

            cluster = await session.get(PromptCluster, incident.prompt_cluster_id)
        if session is not None and org is not None and getattr(org, "id", None) is not None:
            from sqlalchemy import select

            from app.models.core import Signal

            stmt = select(Signal).where(Signal.org_id == org.id, Signal.kind.ilike("%citation%"))
            if getattr(incident, "prompt_cluster_id", None) is not None:
                stmt = stmt.where(Signal.prompt_cluster_id == incident.prompt_cluster_id)
            rows = (
                (await session.execute(stmt.order_by(Signal.observed_at.desc()).limit(200))).scalars().all()
            )
            for r in rows:
                _urls_in(r.raw or {}, sig_urls)
    except Exception as exc:  # DB problems must not abort web collection
        log.warning("web.context.lookup_failed", error=str(exc))

    cited: list[str] = []
    for key in ("cited_urls", "citations", "urls"):
        _urls_in(ctx_json.get(key) or [], cited)
    cited.extend(sig_urls)

    prompts = list(ctx_json.get("prompts") or []) + list(getattr(cluster, "prompts", None) or [])
    prompts = [p if isinstance(p, str) else str(p.get("text") or p.get("prompt") or "") for p in prompts]
    topic = ctx_json.get("topic") or getattr(cluster, "topic", "") or ""
    claim = " ".join(x for x in (getattr(incident, "title", ""), getattr(incident, "summary", "")) if x)
    keywords = [str(k) for k in (ctx_json.get("keywords") or [])] + [
        str(t) for t in (getattr(org, "topics", None) or []) if isinstance(t, str)
    ]
    return IncidentContext(
        org_name=getattr(org, "name", "") or "",
        own_domain=registrable_host(getattr(org, "domain", "") or ctx_json.get("own_domain", "") or ""),
        competitor_domains=_domains(
            getattr(org, "competitor_domains", None) or ctx_json.get("competitor_domains")
        ),
        canonical_domains=_domains(
            getattr(org, "canonical_domains", None) or ctx_json.get("canonical_domains")
        ),
        topic=topic,
        prompts=[p for p in prompts if p],
        cited_urls=list(dict.fromkeys(cited)),
        claim=claim,
        keywords=keywords,
    )


def _domains(value: Any) -> list[str]:
    out = []
    for v in value or []:
        d = registrable_host(
            v if isinstance(v, str) else str(v.get("domain", "")) if isinstance(v, dict) else ""
        )
        if d and d not in out:
            out.append(d)
    return out


def normalize_target(url: str) -> str:
    p = urlsplit(url.strip())
    path = p.path or "/"
    return p._replace(netloc=p.netloc.lower(), path=path, fragment="").geturl()


# ---- snapshot store ----------------------------------------------------------------------------


class EvidenceRowSnapshotStore:
    """Latest prior snapshot per URL read from Evidence rows (storage of record). save() is a no-op because
    collect_evidence persists the new snapshot as an Evidence row itself."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def latest(self, url: str) -> Any | None:
        try:
            from sqlalchemy import select

            from app.models.evidence import Evidence

            stmt = (
                select(Evidence)
                .where(Evidence.url == url, Evidence.content_hash.is_not(None))
                .order_by(Evidence.retrieved_at.desc())
                .limit(1)
            )
            return (await self.session.execute(stmt)).scalars().first()
        except Exception as exc:  # model not merged yet / DB error => no prior snapshot, never fabricate one
            log.info("web.snapshot.lookup_unavailable", url=url, error=str(exc))
            return None

    async def save(self, result: FetchResult) -> None:
        return None


# ---- ranker plumbing ---------------------------------------------------------------------------


def _load_ranker() -> Any | None:
    try:
        from app.evidence.ranker import EvidenceRanker  # A8; optional

        return EvidenceRanker.load()
    except Exception as exc:
        log.info("web.ranker.unavailable", reason=str(exc))
        return None


def _score_with(ranker: Any | None, claim: str, passage: str, meta: dict) -> dict[str, Any] | None:
    if ranker is None or not claim or not passage:
        return None
    try:
        fn = ranker.score if hasattr(ranker, "score") else ranker
        out = fn(claim, passage, meta)
        get = out.get if isinstance(out, dict) else (lambda k, d=None: getattr(out, k, d))
        res = {k: get(k) for k in ("support", "contradiction", "insufficient", "freshness_risk")}
        res["ranker_available"] = getattr(ranker, "available", True)
        # provenance of the score: which model/heuristic produced it and whether it was a degraded fallback
        for k in ("label", "degraded", "method", "version", "freshness_known"):
            v = get(k)
            if v is not None:
                res[k] = v
        return res
    except Exception as exc:
        log.warning("web.ranker.failed", error=str(exc))
        return None


# ---- main entry --------------------------------------------------------------------------------


async def _emit(
    emit: Emit | None, stage: str, status: StepStatus, message: str, meta: dict | None = None
) -> None:
    if emit is None:
        return
    try:
        out = emit(stage, status, message, meta or {})
        if inspect.isawaitable(out):
            await out
    except Exception as exc:  # progress reporting must never break collection
        log.warning("web.emit_failed", stage=stage, error=str(exc))


def _age_days(dt: datetime | None, now: datetime) -> float | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return max(0.0, (now - dt).total_seconds() / 86400)


def _new_row(fields: dict[str, Any]) -> Any:
    try:
        from app.models.evidence import Evidence

        allowed = {k: v for k, v in fields.items() if hasattr(Evidence, k)}
        return Evidence(**allowed)
    except ImportError:
        return EvidenceDraft(**{k: v for k, v in fields.items() if k in EvidenceDraft.__dataclass_fields__})


async def plan_targets(
    ctx: IncidentContext, collector: WebCollector, emit: Emit | None, *, max_pages: int, per_domain: int
) -> list[tuple[str, str]]:
    """Ordered (url, origin) targets: cited URLs, own/canonical pages, then competitors."""
    targets: list[tuple[str, str]] = [(normalize_target(u), "cited") for u in ctx.cited_urls[:15]]
    groups = [("own", [ctx.own_domain] if ctx.own_domain else [])]
    groups += [("canonical", ctx.canonical_domains), ("competitor", ctx.competitor_domains)]
    kws = list({t for t in build_query_terms(ctx) if len(t) > 3})[:10]
    await _emit(emit, "web.discover.started", StepStatus.RUNNING, "Discovering pages from sitemaps", {})
    found = 0
    for origin, domains in groups:
        for d in domains:
            targets.append((f"https://{d}/", origin))
            try:
                urls = await collector.discover_urls(d, limit=per_domain, keywords=kws)
            except Exception as exc:
                await _emit(
                    emit,
                    "web.discover.domain",
                    StepStatus.WARNING,
                    f"Sitemap discovery failed for {d}",
                    {"domain": d, "error": str(exc)},
                )
                continue
            found += len(urls)
            targets.extend((normalize_target(u), origin) for u in urls)
    seen, out = set(), []
    for url, origin in targets:
        if url not in seen:
            seen.add(url)
            out.append((url, origin))
    await _emit(
        emit,
        "web.discover.completed",
        StepStatus.SUCCESS,
        f"{len(out[:max_pages])} pages queued ({found} from sitemaps)",
        {"queued": len(out[:max_pages]), "from_sitemaps": found, "truncated": max(0, len(out) - max_pages)},
    )
    return out[:max_pages]


async def collect_evidence(
    session: Any,
    incident: Any,
    emit: Emit | None = None,
    *,
    collector: WebCollector | None = None,
    ranker: Any | None = None,
    snapshots: Any | None = None,
    max_pages: int = 24,
    per_domain_discovery: int = 4,
    stale_after_days: int = STALE_AFTER_DAYS,
    use_cache: bool = True,
) -> list[Any]:
    """Fetch public pages for the incident and persist Evidence rows (added + flushed, not committed)."""
    ctx = await resolve_context(session, incident)
    owns_collector = collector is None
    collector = collector or WebCollector()
    ranker = ranker if ranker is not None else _load_ranker()
    snapshots = snapshots if snapshots is not None else EvidenceRowSnapshotStore(session)
    incident_id = getattr(incident, "id", None)
    now = datetime.now(UTC)
    rows: list[Any] = []
    try:
        if not (ctx.own_domain or ctx.competitor_domains or ctx.canonical_domains or ctx.cited_urls):
            await _emit(
                emit,
                "web.fetch.started",
                StepStatus.WARNING,
                "No domains or cited URLs available for this incident; nothing to fetch",
                {},
            )
            return []
        terms = build_query_terms(ctx)
        targets = await plan_targets(
            ctx, collector, emit, max_pages=max_pages, per_domain=per_domain_discovery
        )
        await _emit(
            emit,
            "web.fetch.started",
            StepStatus.RUNNING,
            f"Fetching {len(targets)} public pages",
            {"total": len(targets)},
        )

        async def one(url: str, origin: str) -> tuple[str, str, FetchResult]:
            return url, origin, await collector.fetch(url, use_cache=use_cache)

        tasks = [asyncio.create_task(one(u, o)) for u, o in targets]
        counts = {s.value: 0 for s in EvidenceStatus}
        for fut in asyncio.as_completed(tasks):
            url, origin, res = await fut
            prior = await snapshots.latest(url)
            diff = diff_snapshots(prior, res)
            etype = classify_domain(
                res.final_url or url, ctx.own_domain, ctx.competitor_domains, ctx.canonical_domains
            )
            status = _evidence_status(res, diff.changed, now, stale_after_days)
            counts[status] += 1
            blocks = pick_relevant_blocks(res, terms, ctx.topic) if res.ok else []
            row = _build_row(incident_id, ctx, res, etype, status, blocks, diff, ranker, origin, now)
            rows.append(row)
            if session is not None:
                session.add(row)
            await _emit(
                emit,
                "web.fetch.page",
                StepStatus.SUCCESS if res.ok else StepStatus.WARNING,
                f"{etype.value} {registrable_host(url)}: {status}" + (f" ({res.error})" if res.error else ""),
                {
                    "url": url,
                    "status": status,
                    "type": etype.value,
                    "http_status": res.http_status,
                    "relevant_blocks": len(blocks),
                    "from_cache": res.from_cache,
                },
            )
        if session is not None and rows:
            await session.flush()
        failed = counts["failed"] + counts["unavailable"]
        await _emit(
            emit,
            "web.fetch.completed",
            StepStatus.WARNING if failed and failed == len(rows) else StepStatus.SUCCESS,
            f"Fetched {len(rows) - failed}/{len(rows)} pages "
            f"({counts['changed']} changed, {failed} unavailable/failed)",
            {"total": len(rows), **counts, "cache_backend": collector.cache.backend},
        )
        relevant = sum(1 for r in rows if (r.raw or {}).get("relevant_blocks"))
        await _emit(
            emit,
            "web.relevance.completed",
            StepStatus.SUCCESS,
            f"{relevant} pages contain blocks matching the incident topic",
            {"pages_with_relevant_blocks": relevant, "terms": len(terms)},
        )
        rows.sort(key=lambda r: -((r.raw or {}).get("relevance") or 0.0))
        return rows
    finally:
        if owns_collector:
            await collector.aclose()


def _evidence_status(res: FetchResult, changed: bool, now: datetime, stale_days: int) -> str:
    if not res.ok:
        return (
            res.status
            if res.status in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value)
            else "failed"
        )
    if changed:
        return EvidenceStatus.CHANGED.value
    age = _age_days(res.last_modified, now)
    if age is not None and age > stale_days:
        return EvidenceStatus.STALE.value
    return EvidenceStatus.LIVE.value


def _relation(origin: str, etype: EvidenceType, has_blocks: bool, best: dict | None) -> str:
    """How this page relates to the incident (descriptive, deterministic)."""
    if not has_blocks:
        return "fetched_no_relevant_content"
    if origin == "cited":
        return "cited_by_ai_and_on_topic"
    return {EvidenceType.OWNED: "owned_page_on_topic", EvidenceType.COMPETITOR: "competitor_page_on_topic"}.get(
        etype, "third_party_page_on_topic"
    )


def _build_row(
    incident_id: Any,
    ctx: IncidentContext,
    res: FetchResult,
    etype: EvidenceType,
    status: str,
    blocks: list[dict],
    diff: Any,
    ranker: Any | None,
    origin: str,
    now: datetime,
) -> Any:
    best = blocks[0] if blocks else None
    excerpt = ""
    if best:
        excerpt = (f"{best['heading']}: " if best["heading"] else "") + best["text"]
        excerpt = excerpt[:EXCERPT_CHARS]
    scores = None
    if best:
        age = _age_days(res.last_modified, now)
        scores = _score_with(
            ranker,
            ctx.claim or ctx.topic,
            best["text"],
            {
                "url": res.final_url,
                "title": res.title,
                "type": etype.value,
                "owned": etype == EvidenceType.OWNED,
                "source_age_days": age,
                "status": status,
            },
        )
    relevance = best["relevance"] if best else 0.0
    d = diff.to_dict()
    for key in ("added", "removed", "modified"):
        d[key] = [
            {k: (v[:300] if isinstance(v, str) else v) for k, v in item.items()} for item in d[key][:10]
        ]
    raw = {
        "origin": origin,
        "requested_url": res.url,
        "final_url": res.final_url,
        "http_status": res.http_status,
        "error": res.error,
        "content_type": res.content_type,
        "from_cache": res.from_cache,
        "relevance": relevance,
        "relevant_blocks": blocks,
        "block_index": [{"heading": b.heading, "hash": b.hash} for b in res.blocks[:300]],
        "normalized_hash": res.normalized_hash,
        "source_category": classify_source(res.final_url or res.url, ctx.own_domain, ctx.competitor_domains,
                                           ctx.canonical_domains).value,
        "incident_relation": _relation(origin, etype, bool(blocks), best),
        "title": res.title,
        "title_is_evidence": False,  # a page title identifies a page; it is never article evidence
        "has_extract": bool(excerpt),
        "diff": d,
        "last_modified": res.last_modified.isoformat() if res.last_modified else None,
        "ranker": scores,
    }
    host = registrable_host(res.final_url or res.url)
    fields = {
        "incident_id": incident_id,
        "type": etype.value,
        "status": EvidenceStatus(status).value,
        "title": (res.title or host or res.url)[:500],
        "source": host,
        "url": res.final_url if res.ok else res.url,
        "content_hash": res.content_hash if res.ok else None,
        "excerpt": excerpt,
        "retrieval_method": "cache" if res.from_cache else res.retrieval_method,
        "retrieved_at": res.fetched_at,
        "observed_at": res.last_modified or res.fetched_at if res.ok else None,
        "support_score": scores["support"] if scores else None,
        "contradiction_score": scores["contradiction"] if scores else None,
        "insufficient_score": scores["insufficient"] if scores else None,
        "freshness_risk": scores["freshness_risk"] if scores else None,
        "confidence": relevance if res.ok else 0.0,
        "raw": raw,
    }
    return _new_row(fields)


__all__ = [
    "EvidenceDraft",
    "EvidenceRowSnapshotStore",
    "IncidentContext",
    "build_query_terms",
    "classify_domain",
    "collect_evidence",
    "plan_targets",
    "resolve_context",
    "score_text",
]
