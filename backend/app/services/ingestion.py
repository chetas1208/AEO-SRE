"""Profound -> Signal ingestion.

`ingest_org(session, org_id)` resolves the org's Profound category from its domain, pulls tracked prompts,
visibility (headline + topic/model/region/persona segments), competitors, citation share, FactCheck accuracy
and prompt volume, normalizes them (app.connectors.profound.normalize) and upserts `Signal` rows.

Guarantees: nothing is fabricated (no data -> no rows); a failing surface is recorded as degraded/unavailable and
the others continue; idempotent on (org, kind, metric, observed_at, prompt, engine [+ cluster/persona/region/
competitor/segment]) - re-ingesting restates values in place (Profound revises past numbers). Flushes only; the
caller (worker) commits.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors.profound import (
    CitationsQuery,
    FactCheckQuery,
    FileRawPayloadStore,
    NormalizedAsset,
    NormalizedSignal,
    ProfoundClient,
    ProfoundError,
    ProfoundNotConfigured,
    ProfoundRateLimited,
    PromptsListQuery,
    QueryFanoutsQuery,
    RawPayloadStore,
    VisibilityQuery,
    VolumeQuery,
    normalize_assets,
    normalize_categories,
    normalize_citations,
    normalize_factcheck,
    normalize_fanouts,
    normalize_prompts,
    normalize_visibility,
    normalize_volume,
)
from app.connectors.profound.capabilities import DOC_CONFIDENCE, ENDPOINTS, status_from_error
from app.connectors.profound.normalize import NormalizedPrompt, domain_of, normalize_topics
from app.connectors.profound.timeutil import ET, et_midnight_utc, last_complete_day
from app.core.config import ROOT
from app.domain.enums import CapabilityState
from app.domain.source_mode import SignalSourceMode

log = structlog.get_logger(__name__)

SURFACES = ("prompts", "visibility", "competitors", "citations", "factcheck", "prompt_volume", "query_fanouts")
# Stage 1 (scheduled, cheap): aggregates only. Stage 2 (on demand, when an incident warrants it): search queries and
# accuracy claims. Full answers / citation details are fetched by investigation.answers, never by scheduled ingest.
MONITORING_SURFACES = ("prompts", "visibility", "competitors", "citations", "prompt_volume")
DEEP_SURFACES = ("factcheck", "query_fanouts")
RUN_HISTORY_LIMIT = 30
RAW_DIR = ROOT / "data" / "profound_raw"


@dataclass
class IngestConfig:
    lookback_days: int = 21  # first sync window (complete ET days)
    restate_days: int = 3  # incremental syncs re-pull this many days (Profound revises recent numbers)
    max_pages: int = 10  # per report call; v2 pages hold <= 50 rows
    segments: tuple[str, ...] = ("topic", "model", "region", "persona")
    prompt_level: bool = False  # per-prompt visibility is row-heavy against the 600 req/h limit
    competitor_by_topic: bool = True
    max_competitors: int = 5
    max_volume_keywords: int = 8
    volume_window_days: int = 56
    pull: tuple[str, ...] = SURFACES
    stage: str = "full"  # monitoring | deep | full

    @classmethod
    def monitoring(cls, **kw: Any) -> IngestConfig:
        return cls(pull=MONITORING_SURFACES, stage="monitoring", **kw)

    @classmethod
    def deep(cls, **kw: Any) -> IngestConfig:
        kw.setdefault("lookback_days", 14)
        return cls(pull=DEEP_SURFACES, stage="deep", **kw)


@dataclass
class SurfaceOutcome:
    state: CapabilityState = CapabilityState.HEALTHY
    reason: str | None = None
    rows: int = 0
    errors: list[str] = field(default_factory=list)

    def fail(self, e: BaseException, label: str = "") -> None:
        st, reason = status_from_error(e)
        self.errors.append(f"{label}{reason}".strip(":"))
        self._worse(st, reason)

    def _worse(self, st: CapabilityState, reason: str) -> None:
        order = {CapabilityState.HEALTHY: 0, CapabilityState.DEGRADED: 1, CapabilityState.UNAVAILABLE: 2}
        if self.reason is None or order[st] > order[self.state]:
            self.state, self.reason = st, reason

    def finalize(self) -> None:
        # some data arrived despite errors -> degraded, not unavailable
        if self.errors and self.rows and self.state == CapabilityState.UNAVAILABLE:
            self.state = CapabilityState.DEGRADED
        if self.errors:
            self.reason = "; ".join(dict.fromkeys(self.errors))
        elif self.rows == 0 and self.reason is None:  # keep a more specific reason (e.g. no_competitor_assets_tracked)
            self.reason = "no_rows_returned"


@dataclass
class IngestResult:
    org_id: str
    status: str  # ok | degraded | unavailable | failed
    category: dict[str, str] | None = None
    window: tuple[str, str] | None = None
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    surfaces: dict[str, dict[str, Any]] = field(default_factory=dict)
    error: str | None = None
    run_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    source_mode: str = SignalSourceMode.LIVE.value
    stage: str = "full"
    checkpoints: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "org_id": self.org_id, "status": self.status, "category": self.category,
            "window": list(self.window) if self.window else None, "created": self.created,
            "updated": self.updated, "unchanged": self.unchanged, "surfaces": self.surfaces, "error": self.error,
            "run_id": self.run_id, "source_mode": self.source_mode, "stage": self.stage,
            "checkpoints": self.checkpoints,
        }


# -- helpers --------------------------------------------------------------------------------------------


def dedupe_key(s: NormalizedSignal) -> str:
    parts = [s.kind, s.metric, s.observed_at.astimezone(UTC).isoformat(), s.prompt or "", s.engine or "",
             s.cluster or "", s.persona or "", s.region or "", s.competitor or "", s.segment or ""]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:40]


def _same(a: Any, b: Any) -> bool:
    """Equality that ignores float summation noise (Profound re-serves identical aggregates differing in the last
    digit), so an unchanged re-pull is reported as unchanged, not as a restatement."""
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) is type(b) and a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, list) and isinstance(b, list):
        return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


async def _get_setting(session: AsyncSession, key: str) -> dict[str, Any] | None:
    from app.models.core import Setting

    row = await session.get(Setting, key)
    return dict(row.value) if row and row.value else None


async def _put_setting(session: AsyncSession, key: str, value: dict[str, Any]) -> None:
    """Upsert a Setting. A concurrent writer winning the insert (PK collision) turns this into an update."""
    from sqlalchemy.exc import IntegrityError

    from app.models.core import Setting

    row = await session.get(Setting, key)
    if row is None:
        try:
            async with session.begin_nested():
                session.add(Setting(key=key, value=value))
                await session.flush()
            return
        except IntegrityError:
            row = await session.get(Setting, key, populate_existing=True)
            if row is None:
                raise
    row.value = value


async def _resolve_category(
    client: ProfoundClient, org_domain: str, cached_id: str | None
) -> tuple[dict[str, str], list[NormalizedAsset]] | None:
    """Find the category whose OWNED asset matches the org domain. Returns None when none matches."""
    cats = normalize_categories((await client.list_categories()).data)
    cats.sort(key=lambda c: c.id != cached_id)  # cached first
    for c in cats[:10]:
        try:
            assets = normalize_assets((await client.list_category_assets(c.id)).data)
        except ProfoundError:
            continue
        for a in assets:
            if not a.is_owned:
                continue
            doms = {domain_of(a.website), *(domain_of(x) for x in a.alternate_domains)}
            if org_domain in doms or any(org_domain.endswith("." + d) for d in doms if d):
                return {"id": c.id, "name": c.name}, assets
    return None


async def _collect(it, normalizer, outcome: SurfaceOutcome, label: str, **kw) -> list[NormalizedSignal]:
    sigs: list[NormalizedSignal] = []
    try:
        async for resp in it:
            sigs.extend(normalizer(resp.data, raw_ref=resp.raw_ref, **kw))
    except ProfoundError as e:
        outcome.fail(e, f"{label}:")
    return sigs


def _to_cluster_prompts(prompts: list[NormalizedPrompt]) -> dict[str, list[dict[str, Any]]]:
    by: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in prompts:
        by[p.topic or "General"].append({
            "id": p.id, "text": p.text, "status": p.status, "regions": p.regions, "personas": p.personas,
            "platforms": p.platforms, "tags": p.tags,
        })
    return by


async def _upsert_clusters(
    session: AsyncSession, org_id: uuid.UUID, topics: dict[str, list[dict[str, Any]]]
) -> dict[str, uuid.UUID]:
    from app.models.core import PromptCluster

    rows = (await session.execute(select(PromptCluster).where(PromptCluster.org_id == org_id))).scalars().all()
    by_name = {r.topic.lower(): r for r in rows}
    for topic, prompts in topics.items():
        row = by_name.get(topic.lower())
        if row is None:
            row = PromptCluster(org_id=org_id, topic=topic, prompts=prompts)
            session.add(row)
            by_name[topic.lower()] = row
        elif prompts:
            row.prompts = prompts
    await session.flush()
    return {k: r.id for k, r in by_name.items()}


async def _upsert_signals(
    session: AsyncSession, org_id: uuid.UUID, sigs: list[NormalizedSignal], cluster_ids: dict[str, uuid.UUID],
    prompt_topic: dict[str, str], result: IngestResult, source_mode: str = SignalSourceMode.LIVE.value,
) -> None:
    from app.models.core import Signal

    if not sigs:
        return
    # unseen topic names get a (prompt-less) cluster so segment series keep their cluster identity
    missing = {s.cluster for s in sigs if s.cluster and s.cluster.lower() not in cluster_ids}
    if missing:
        cluster_ids.update(await _upsert_clusters(session, org_id, {t: [] for t in missing}))
    lo = min(s.observed_at for s in sigs) - timedelta(days=1)
    hi = max(s.observed_at for s in sigs) + timedelta(days=1)
    kinds = {s.kind for s in sigs}
    existing = (
        await session.execute(
            select(Signal).where(
                Signal.org_id == org_id, Signal.kind.in_(kinds), Signal.observed_at >= lo, Signal.observed_at <= hi
            )
        )
    ).scalars().all()
    by_key = {(r.raw or {}).get("dedupe_key"): r for r in existing if (r.raw or {}).get("dedupe_key")}
    seen: set[str] = set()
    fresh: list[Signal] = []
    for s in sigs:
        if not s.cluster and s.prompt_id and s.prompt_id in prompt_topic:
            s.cluster = prompt_topic[s.prompt_id]
        key = dedupe_key(s)
        if key in seen:
            continue
        seen.add(key)
        raw = {
            "dedupe_key": key, "segment": s.segment, "cluster": s.cluster, "engine": s.engine, "region": s.region,
            "persona": s.persona, "competitor": s.competitor, "prompt": s.prompt, "prompt_id": s.prompt_id,
            "row": s.extra, "source_mode": source_mode, "ingestion_run_id": result.run_id,
        }
        raw = {k: v for k, v in raw.items() if v not in (None, {})}
        cid = cluster_ids.get(s.cluster.lower()) if s.cluster else None
        row = by_key.get(key)
        if row is None:
            fresh.append(Signal(
                org_id=org_id, kind=s.kind, source=s.source, metric=s.metric, value=s.value, baseline=s.baseline,
                observed_at=s.observed_at, prompt_cluster_id=cid, raw=raw, raw_payload_ref=s.raw_ref,
            ))
            result.created += 1
        elif (not _same(row.value, s.value) or not _same((row.raw or {}).get("row"), s.extra)
              or (row.raw or {}).get("source_mode") != source_mode):
            row.value, row.raw, row.raw_payload_ref, row.prompt_cluster_id = s.value, raw, s.raw_ref, cid
            result.updated += 1
        else:
            result.unchanged += 1
    await session.flush()  # restatements of existing rows first (outside the savepoint)
    # Inserts run in a SAVEPOINT: a concurrent ingest that already wrote the same (org, source, idempotency_key) trips
    # the unique index; that row is a duplicate to skip, never a crash.
    from sqlalchemy.exc import IntegrityError

    if fresh:
        try:
            async with session.begin_nested():
                session.add_all(fresh)
                await session.flush()
        except IntegrityError:
            for obj in fresh:
                if obj in session:
                    session.expunge(obj)
            for obj in fresh:
                try:
                    async with session.begin_nested():
                        session.add(obj)
                        await session.flush()
                except IntegrityError:
                    if obj in session:
                        session.expunge(obj)
                    result.created -= 1
                    result.unchanged += 1
    await session.flush()


# -- main ----------------------------------------------------------------------------------------------


async def ingest_org(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    client: ProfoundClient | None = None,
    config: IngestConfig | None = None,
    raw_store: RawPayloadStore | None = None,
    now: datetime | None = None,
    source_mode: SignalSourceMode | str = SignalSourceMode.LIVE,
) -> IngestResult:
    """Ingest one org. `source_mode` is declared by the caller (LIVE for the real API, REPLAY/FIXTURE for recorded
    or fixture clients) and persisted on every signal; a failing live pull is recorded as failed/unavailable and
    NEVER backfilled from fixtures."""
    from app.models.core import Organization

    cfg = config or IngestConfig()
    now = now or datetime.now(UTC)
    result = IngestResult(
        org_id=str(org_id), status="failed", source_mode=SignalSourceMode(str(source_mode).upper()).value,
        stage=cfg.stage,
    )
    org = await session.get(Organization, org_id)
    if org is None:
        result.error = "organization_not_found"
        return result
    owns_client = client is None
    if client is None:
        client = ProfoundClient(raw_store=raw_store or FileRawPayloadStore(RAW_DIR))
    started_at = now
    try:
        return await _ingest(session, org, client, cfg, now, result, started_at=started_at)
    except Exception as e:
        log.exception("ingest.failed", org_id=str(org_id))
        result.status, result.error = "failed", f"{type(e).__name__}: {e}"[:300]
        try:
            await _record(
                session, org_id, result, now, await _get_setting(session, f"profound.last_sync.{org_id}"),
                ok=False, started_at=started_at,
            )
        except Exception:  # noqa: BLE001 - session may be unusable after a DB error
            log.warning("ingest.record_failed", org_id=str(org_id))
        return result
    finally:
        if owns_client:
            await client.aclose()


async def _record(
    session,
    org_id,
    result: IngestResult,
    now: datetime,
    prev: dict | None,
    ok: bool,
    *,
    started_at: datetime | None = None,
) -> None:
    """Persist the run (id, provider, source_mode, timings, counts, errors, checkpoint) and the last-sync summary.

    Storage is the `settings` table until B1 adds a dedicated `ingestion_runs` table (docs/notes/schema-requests.md);
    the record shape already matches the requested columns.
    """
    success = (prev or {}).get("success")
    if ok:
        success = {"at": now.isoformat(), "window": list(result.window) if result.window else None}
    records_seen = sum(int((s or {}).get("rows") or 0) for s in (result.surfaces or {}).values())
    error_count = sum(
        1 for s in (result.surfaces or {}).values() if (s or {}).get("state") not in (None, "healthy", CapabilityState.HEALTHY.value)
    )
    if result.error and not error_count:
        error_count = 1
    # per-surface resume points: only a surface that finished without errors advances its checkpoint
    checkpoints = dict((prev or {}).get("checkpoints") or {})
    checkpoints.update(result.checkpoints or {})
    run = {
        "id": result.run_id,
        "org_id": str(org_id),
        "provider": "profound",
        "source_mode": result.source_mode,
        "stage": result.stage,
        "started_at": (started_at or now).isoformat(),
        "completed_at": datetime.now(UTC).isoformat(),
        "status": result.status,
        "records_seen": records_seen,
        "records_normalized": result.created + result.updated + result.unchanged,
        "records_created": result.created,
        "records_updated": result.updated,
        "error_count": error_count,
        "checkpoint": checkpoints,
        "window": list(result.window) if result.window else None,
        "error": result.error,
    }
    await _put_setting(session, f"profound.last_sync.{org_id}", {
        "last_attempt": now.isoformat(), "status": result.status, "success": success,
        "source_mode": result.source_mode, "checkpoints": checkpoints,
        "counts": {"created": result.created, "updated": result.updated, "unchanged": result.unchanged},
        "run": run,
        "error": result.error,
    })
    hist = list((await _get_setting(session, f"profound.runs.{org_id}") or {}).get("runs") or [])
    hist = [r for r in hist if r.get("id") != result.run_id]
    hist.append(run)
    await _put_setting(session, f"profound.runs.{org_id}", {"runs": hist[-RUN_HISTORY_LIMIT:]})
    prev_caps = ((await _get_setting(session, f"profound.capabilities.{org_id}")) or {}).get("surfaces") or {}
    await _put_setting(session, f"profound.capabilities.{org_id}", {
        "at": now.isoformat(), "surfaces": {**prev_caps, **(result.surfaces or {})},
    })


async def run_scheduled_ingest(
    session: AsyncSession, org_id: uuid.UUID, *, client: ProfoundClient | None = None, now: datetime | None = None,
    source_mode: SignalSourceMode | str = SignalSourceMode.LIVE,
) -> IngestResult:
    """THE scheduled ingestion entrypoint (stage 1: cheap monitoring pull). The worker job (pipeline.ingest) and
    `make ingest-live` both call exactly this, so a manual run exercises the production path."""
    return await ingest_org(
        session, org_id, client=client, config=IngestConfig.monitoring(), now=now, source_mode=source_mode
    )


async def ingest_deep(
    session: AsyncSession, org_id: uuid.UUID, *, client: ProfoundClient | None = None, now: datetime | None = None,
    source_mode: SignalSourceMode | str = SignalSourceMode.LIVE,
) -> IngestResult:
    """Stage 2: query fanouts + FactCheck accuracy. Called from investigation only after an incident exists."""
    return await ingest_org(session, org_id, client=client, config=IngestConfig.deep(), now=now, source_mode=source_mode)


async def list_ingestion_runs(session: AsyncSession, org_id: uuid.UUID) -> list[dict[str, Any]]:
    """Newest first."""
    return list(reversed(list((await _get_setting(session, f"profound.runs.{org_id}") or {}).get("runs") or [])))


def _surface_report(outcomes: dict[str, SurfaceOutcome], now: datetime) -> dict[str, dict[str, Any]]:
    rep = {}
    for name, o in outcomes.items():
        o.finalize()
        rep[name] = {
            "state": o.state.value, "reason": o.reason, "rows": o.rows, "endpoint": ENDPOINTS.get(name),
            "verification": "live_verified" if o.state == CapabilityState.HEALTHY and o.rows else "unverified",
            "doc_confidence": DOC_CONFIDENCE.get(name), "checked_at": now.isoformat(),
        }
    return rep


async def _ingest(
    session, org, client: ProfoundClient, cfg: IngestConfig, now: datetime, result: IngestResult, *, started_at: datetime
):
    org_id = org.id
    prev = await _get_setting(session, f"profound.last_sync.{org_id}")
    outcomes = {s: SurfaceOutcome() for s in cfg.pull}

    def unavailable(reason: str, state: CapabilityState = CapabilityState.UNAVAILABLE) -> IngestResult:
        for o in outcomes.values():
            o.state, o.reason = state, reason
        result.surfaces = {
            n: {"state": o.state.value, "reason": reason, "rows": 0, "endpoint": ENDPOINTS.get(n),
                "verification": "unverified", "doc_confidence": DOC_CONFIDENCE.get(n), "checked_at": now.isoformat()}
            for n, o in outcomes.items()
        }
        result.status = "unavailable"
        return result

    if not client.configured:
        res = unavailable("not_configured")
        await _record(session, org_id, res, now, prev, ok=False, started_at=started_at)
        return res

    # -- category discovery
    cached = await _get_setting(session, f"profound.category.{org_id}")
    try:
        found = await _resolve_category(client, domain_of(org.domain), (cached or {}).get("id"))
    except ProfoundNotConfigured:
        res = unavailable("not_configured")
        await _record(session, org_id, res, now, prev, ok=False, started_at=started_at)
        return res
    except ProfoundError as e:
        st, reason = status_from_error(e)
        res = unavailable(f"category_discovery:{reason}", st)
        res.error = str(e)
        await _record(session, org_id, res, now, prev, ok=False, started_at=started_at)
        return res
    if found is None:
        reason = f"no_profound_category_owns_{domain_of(org.domain)}"
        res = unavailable(reason)
        res.error = reason  # lets passive health tell "org not mapped to Profound" from a Profound fault
        await _record(session, org_id, res, now, prev, ok=False, started_at=started_at)
        return res
    category, assets = found
    result.category = category
    await _put_setting(session, f"profound.category.{org_id}", {"id": category["id"], "name": category["name"]})
    cat_id = category["id"]

    # -- window (ET, inclusive). Resume from per-surface checkpoints: a surface that failed last time keeps its old
    # checkpoint, so the next run re-pulls its gap instead of skipping it.
    end = last_complete_day(now)
    lookback_start = end - timedelta(days=cfg.lookback_days - 1)
    checkpoints = dict((prev or {}).get("checkpoints") or {})
    last_ok = ((prev or {}).get("success") or {}).get("at")
    legacy_day = None
    if last_ok:
        try:
            legacy_day = datetime.fromisoformat(last_ok).astimezone(ET).date()
        except ValueError:
            pass
    starts = []
    for surface in cfg.pull:
        cp = checkpoints.get(surface)
        try:
            cp_day = date.fromisoformat(cp) if cp else (legacy_day if not checkpoints else None)
        except ValueError:
            cp_day = None
        starts.append(max(lookback_start, cp_day - timedelta(days=cfg.restate_days)) if cp_day else lookback_start)
    start = min(starts) if starts else lookback_start
    start = min(start, end)
    s_iso, e_iso = start.isoformat(), end.isoformat()
    result.window = (s_iso, e_iso)
    default_at = et_midnight_utc(end)
    signals: list[NormalizedSignal] = []

    # -- prompts / topics
    prompts: list[NormalizedPrompt] = []
    topics: dict[str, list[dict[str, Any]]] = {}
    if "prompts" in cfg.pull:
        o = outcomes["prompts"]
        cursor: str | None = None
        try:
            for _ in range(cfg.max_pages):
                q = PromptsListQuery(limit=1000, cursor=cursor, status=["active"])
                resp = await client.list_prompts(cat_id, q)
                prompts.extend(normalize_prompts(resp.data))
                cursor = resp.next_cursor
                if not cursor:
                    break
        except ProfoundError as e:
            o.fail(e)
        o.rows = len(prompts)
        topics = _to_cluster_prompts(prompts)
        try:
            for t in normalize_topics((await client.list_topics(cat_id)).data):
                topics.setdefault(t, [])
        except ProfoundError:
            pass  # topics are an enrichment of the prompt list; its failure is not a surface failure
    prompt_topic = {p.id: p.topic for p in prompts if p.topic}
    cluster_ids = await _upsert_clusters(session, org_id, topics) if topics else {}
    if prompts:  # versioned, stable prompt-cluster membership (never reshuffled silently)
        try:
            from app.incidents.clustering import record_cluster_version

            await record_cluster_version(session, org_id, [
                {"id": p.id, "text": p.text, "topic": p.topic, "language": p.language} for p in prompts
            ], now=now)
        except Exception:  # noqa: BLE001 - clustering metadata must never fail ingestion
            log.warning("ingest.cluster_version_failed", org_id=str(org_id))

    # -- visibility
    if "visibility" in cfg.pull:
        o = outcomes["visibility"]
        variants: list[list[str]] = [["date"]] + [["date", s] for s in cfg.segments if s in
                                                  ("topic", "model", "region", "persona")]
        if cfg.prompt_level:
            variants.append(["date", "prompt"])
        for gb in variants:
            q = VisibilityQuery(category_id=cat_id, start_date=s_iso, end_date=e_iso, group_by=gb, limit=50,
                                metrics=["visibility_score", "share_of_voice", "average_position"])
            signals += await _collect(client.iter_visibility(q, max_pages=cfg.max_pages), normalize_visibility, o,
                                      "+".join(gb[1:]) or "headline", default_observed_at=default_at)
        o.rows = sum(1 for s in signals if s.kind == "visibility")

    # -- competitors (non-owned assets)
    if "competitors" in cfg.pull:
        o = outcomes["competitors"]
        comp = [a for a in assets if not a.is_owned and a.name]
        configured = {domain_of(d) for d in (org.competitor_domains or [])}
        comp.sort(key=lambda a: domain_of(a.website) not in configured)
        names = [a.name for a in comp[: cfg.max_competitors]]
        if not names:
            o.state, o.reason = CapabilityState.DEGRADED, "no_competitor_assets_tracked"
        else:
            before = len(signals)
            for gb in (["date"], ["date", "topic"]) if cfg.competitor_by_topic else (["date"],):
                q = VisibilityQuery(category_id=cat_id, start_date=s_iso, end_date=e_iso, group_by=gb, limit=50,
                                    scope="all", assets=names, metrics=["share_of_voice", "visibility_score"])
                signals += await _collect(client.iter_visibility(q, max_pages=cfg.max_pages), normalize_visibility,
                                          o, "competitors:" + "+".join(gb[1:]), default_observed_at=default_at,
                                          competitor_names=set(names))
            o.rows = sum(1 for s in signals[before:] if s.kind == "competitor")
            # owned rows echoed by the competitor query are already covered by the visibility surface
            signals[before:] = [s for s in signals[before:] if s.kind == "competitor"]

    # -- citations
    if "citations" in cfg.pull:
        o = outcomes["citations"]
        before = len(signals)
        for gb in (["date"], ["date", "topic"]):
            q = CitationsQuery(category_id=cat_id, start_date=s_iso, end_date=e_iso, entity="domain", group_by=gb,
                               limit=50, scope="owned", metrics=["citation_share", "count"])
            signals += await _collect(client.iter_citations(q, max_pages=cfg.max_pages), normalize_citations, o,
                                      "+".join(gb[1:]) or "headline", default_observed_at=default_at)
        o.rows = len(signals) - before

    # -- factcheck
    if "factcheck" in cfg.pull:
        o = outcomes["factcheck"]
        before = len(signals)
        for gb in (["date"], ["date", "topic"]):
            q = FactCheckQuery(category_id=cat_id, start_date=s_iso, end_date=e_iso, group_by=gb, limit=50)
            signals += await _collect(client.iter_factcheck(q, max_pages=cfg.max_pages), normalize_factcheck, o,
                                      "+".join(gb[1:]) or "headline", default_observed_at=default_at)
        o.rows = len(signals) - before

    # -- query fanouts (read-only). Small variations are stored; only a material shift becomes evidence later.
    if "query_fanouts" in cfg.pull:
        o = outcomes["query_fanouts"]
        before = len(signals)
        q = QueryFanoutsQuery(
            category_id=cat_id, start_date=s_iso, end_date=e_iso, group_by=["date", "prompt", "query"],
            metrics=["share"], limit=50,
        )
        signals += await _collect(
            client.iter_query_fanouts(q, max_pages=min(2, cfg.max_pages)), normalize_fanouts, o,
            "fanouts", default_observed_at=default_at,
        )
        o.rows = len(signals) - before

    # -- prompt volume (keyword-level; keywords = tracked topic names — our mapping, marked unverified)
    if "prompt_volume" in cfg.pull:
        o = outcomes["prompt_volume"]
        before = len(signals)
        vstart = end - timedelta(days=cfg.volume_window_days)
        for topic in list(topics)[: cfg.max_volume_keywords]:
            try:
                resp = await client.prompt_volume(VolumeQuery(keyword=topic, start_date=vstart, end_date=end))
            except ProfoundRateLimited as e:
                o.fail(e, f"{topic}:")
                break  # daily keyword allowance / hourly limit: stop spending it
            except ProfoundError as e:
                o.fail(e, f"{topic}:")
                continue
            signals += normalize_volume(resp.data, keyword=topic, cluster=topic, raw_ref=resp.raw_ref)
        o.rows = len(signals) - before
        if not topics:
            o.state, o.reason = CapabilityState.DEGRADED, "no_topics_to_use_as_keywords"

    await _upsert_signals(session, org_id, signals, cluster_ids, prompt_topic, result, result.source_mode)

    # canonical/competitor domains: only fill when the operator has not set them
    if not (org.canonical_domains or []):
        doms = sorted({d for a in assets if a.is_owned for d in (domain_of(a.website), *map(domain_of, a.alternate_domains)) if d})
        if doms:
            org.canonical_domains = doms
    if not (org.competitor_domains or []):
        doms = sorted({domain_of(a.website) for a in assets if not a.is_owned and a.website} - {""})
        if doms:
            org.competitor_domains = doms

    result.surfaces = _surface_report(outcomes, now)
    result.checkpoints = {
        name: e_iso for name, o in outcomes.items() if not o.errors and o.state == CapabilityState.HEALTHY
    }
    from app.connectors.profound.health import counts_as_healthy

    # account-configuration gaps (no competitor assets tracked) stay visible per surface but do not degrade the run
    states = {"healthy" if counts_as_healthy(v) else v["state"] for v in result.surfaces.values()}
    got_data = (result.created + result.updated + result.unchanged) > 0
    if states == {"healthy"}:
        result.status = "ok"
    elif got_data or "healthy" in states:
        result.status = "degraded"
    else:
        result.status = "unavailable"
    await _record(session, org_id, result, now, prev, ok=got_data, started_at=started_at)
    await session.flush()
    return result

