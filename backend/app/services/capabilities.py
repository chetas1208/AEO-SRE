"""Capability aggregation. Every entry is backed by a real check; missing modules report `unavailable`."""

import asyncio
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
import structlog
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import adapters
from app.core.config import ROOT, get_settings
from app.core.db import get_sessionmaker, utcnow
from app.domain.enums import CapabilityState as CS
from app.models.core import Job, Setting
from app.schemas.system import CapabilitiesOut, Capability

log = structlog.get_logger()
CHECK_TIMEOUT = 15.0
ARTIFACT_DIR = ROOT / "backend" / "ml" / "artifacts" / "evidence_ranker"
INGEST_KINDS = ("ingest_profound_signals", "ingest_mixpanel_events")


def _next_ingest() -> datetime:
    from app.workers.worker import next_ingest_at

    return next_ingest_at()
INVESTIGATE_KINDS = (
    "investigate_incident",
    "fetch_external_evidence",
    "generate_hypotheses",
    "rank_evidence",
)


async def _job_history(
    session: AsyncSession, kinds: tuple[str, ...]
) -> tuple[datetime | None, str | None, datetime | None]:
    ok = (
        await session.execute(
            select(func.max(Job.updated_at)).where(Job.kind.in_(kinds), Job.status == "success")
        )
    ).scalar()
    failed = (
        await session.execute(
            select(Job.error, Job.updated_at)
            .where(Job.kind.in_(kinds), Job.status == "failed")
            .order_by(Job.updated_at.desc())
            .limit(1)
        )
    ).first()
    err, err_at = (failed[0], failed[1]) if failed else (None, None)
    if err_at and ok and ok > err_at:
        err, err_at = None, None  # recovered since
    return ok, err, err_at


async def check_database(session: AsyncSession) -> Capability:
    t0 = time.perf_counter()
    try:
        await session.execute(text("SELECT 1"))
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return Capability(
            key="database", label="Database", state=CS.HEALTHY, last_success=utcnow(), meta={"latency_ms": ms}
        )
    except Exception as exc:
        return Capability(
            key="database",
            label="Database",
            state=CS.UNAVAILABLE,
            last_error=repr(exc),
            last_error_at=utcnow(),
        )


async def check_redis() -> Capability:
    import redis.asyncio as aioredis

    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2)
    t0 = time.perf_counter()
    try:
        await client.ping()
        ms = round((time.perf_counter() - t0) * 1000, 1)
        return Capability(
            key="redis", label="Redis", state=CS.HEALTHY, last_success=utcnow(), meta={"latency_ms": ms}
        )
    except Exception as exc:
        return Capability(
            key="redis", label="Redis", state=CS.UNAVAILABLE, last_error=repr(exc), last_error_at=utcnow()
        )
    finally:
        await client.aclose()


async def check_workers(session: AsyncSession) -> Capability:
    import redis.asyncio as aioredis

    ok, err, err_at = await _job_history(session, tuple(set(INGEST_KINDS + INVESTIGATE_KINDS)))
    queued = (
        await session.execute(select(func.count()).select_from(Job).where(Job.status == "queued"))
    ).scalar_one()
    client = aioredis.from_url(get_settings().redis_url, socket_connect_timeout=2, decode_responses=True)
    try:
        beat = await client.get("arq:queue:health-check")
    except Exception as exc:
        return Capability(
            key="workers",
            label="Workers",
            state=CS.UNAVAILABLE,
            detail="redis unreachable; cannot read worker heartbeat",
            last_success=ok,
            last_error=repr(exc),
            last_error_at=utcnow(),
            meta={"queued_jobs": queued},
        )
    finally:
        await client.aclose()
    if beat:
        return Capability(
            key="workers",
            label="Workers",
            state=CS.HEALTHY,
            detail=str(beat)[:200],
            last_success=ok,
            last_error=err,
            last_error_at=err_at,
            meta={"queued_jobs": queued},
        )
    return Capability(
        key="workers",
        label="Workers",
        state=CS.UNAVAILABLE,
        detail="no worker heartbeat; start with `arq app.workers.worker.WorkerSettings`",
        last_success=ok,
        last_error=err,
        last_error_at=err_at,
        meta={"queued_jobs": queued},
    )


async def profound_sync_status(session: AsyncSession) -> dict[str, Any]:
    """Per-org last sync from Setting rows `profound.last_sync.<org_id>` (app.services.ingestion)."""
    rows = (
        (await session.execute(select(Setting).where(Setting.key.like("profound.last_sync.%"))))
        .scalars()
        .all()
    )
    orgs: dict[str, Any] = {}
    latest_ok: str | None = None
    for r in rows:
        v = r.value or {}
        orgs[r.key.removeprefix("profound.last_sync.")] = v
        at = (v.get("success") or {}).get("at")
        if at and (latest_ok is None or at > latest_ok):
            latest_ok = at
    return {"orgs": orgs, "last_success_at": latest_ok}


def _parse_dt(v: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(v) if v else None
    except ValueError:
        return None


async def check_profound(session: AsyncSession) -> Capability:
    s = get_settings()
    ok, err, err_at = await _job_history(session, INGEST_KINDS)
    sync = await profound_sync_status(session)
    ok = _parse_dt(sync["last_success_at"]) or ok
    base_meta: dict[str, Any] = {"last_sync": sync["orgs"]}
    mod = adapters.optional("app.connectors.profound")
    if not s.profound_api_key:
        # expected state before the key arrives: reported once here, never logged per poll
        return Capability(
            key="profound", label="Profound", state=CS.UNAVAILABLE, detail="PROFOUND_API_KEY not configured",
            last_success=ok, last_error=err, last_error_at=err_at,
            meta={**base_meta, "health_state": "NOT_CONFIGURED", "source_mode": None},
        )  # fmt: skip
    if mod is None or not hasattr(mod, "ProfoundClient"):
        return Capability(
            key="profound", label="Profound", state=CS.UNAVAILABLE, detail="connector module not installed",
            last_success=ok, last_error=err, last_error_at=err_at, meta=base_meta,
        )  # fmt: skip
    client = None
    try:
        client = mod.ProfoundClient()
        report = await client.capability_report()
        surfaces = {k: v.to_dict() for k, v in report.items()}
        states = {k: CS(v["state"]) for k, v in surfaces.items()}
    except Exception as exc:
        return Capability(
            key="profound", label="Profound", state=CS.UNAVAILABLE, detail="capability probe failed",
            last_success=ok, last_error=repr(exc), last_error_at=utcnow(),
            meta={**base_meta, "health_state": "DEGRADED"},
        )  # fmt: skip
    finally:
        if client is not None:
            await client.aclose()
    # agents is optional (Profound Agent executor); it must not drag the connector down on its own
    from app.connectors.profound.health import counts_as_healthy

    core = [
        CS.HEALTHY if counts_as_healthy(surfaces[k]) else v for k, v in states.items() if k != "agents"
    ] or list(states.values())
    if not core:
        state = CS.DEGRADED
    elif all(v == CS.HEALTHY for v in core):
        state = CS.HEALTHY
    elif all(v == CS.UNAVAILABLE for v in core):
        state = CS.UNAVAILABLE
    else:
        state = CS.DEGRADED
    reasons = sorted({v["reason"] for v in surfaces.values() if v.get("reason")})
    from app.connectors.profound.health import derive_health

    health = derive_health(True, surfaces)
    return Capability(
        key="profound", label="Profound", state=state, detail="; ".join(reasons)[:300] or None,
        last_success=ok, last_error=err, last_error_at=err_at,
        meta={**base_meta, "surfaces": surfaces, "health_state": health.value, "source_mode": "LIVE"},
    )  # fmt: skip


async def check_mixpanel(session: AsyncSession) -> Capability:
    from app.integrations.mixpanel.auth import mixpanel_configured
    from app.integrations.mixpanel.client import MixpanelClient
    from app.integrations.mixpanel.cursor import global_last_sync
    from app.integrations.mixpanel.health import MixpanelHealth, derive_health

    s = get_settings()
    ok, err, err_at = await _job_history(session, ("ingest_mixpanel_events",))
    last_sync = await global_last_sync(session)
    lag = (utcnow() - last_sync).total_seconds() if last_sync else None
    if not mixpanel_configured(s):
        state, meta = derive_health(
            configured=False, auth_ok=None, rate_limited=False,
            last_sync_at=last_sync.isoformat() if last_sync else None, lag_seconds=lag,
        )
        return Capability(
            key="mixpanel", label="Mixpanel", state=CS.UNAVAILABLE,
            detail="Mixpanel service account not configured",
            last_success=ok or last_sync, last_error=err, last_error_at=err_at,
            meta={"health_state": state, **meta},
        )
    client = MixpanelClient(s)
    auth_ok, latency_ms, auth_err = await client.ping_auth()
    mp_state, meta = derive_health(
        configured=True,
        auth_ok=auth_ok,
        rate_limited=auth_err == "rate limited",
        last_sync_at=last_sync.isoformat() if last_sync else None,
        lag_seconds=lag,
        error=auth_err,
    )
    cs = CS.HEALTHY if mp_state == MixpanelHealth.READY else (
        CS.UNAVAILABLE if mp_state in (MixpanelHealth.NOT_CONFIGURED, MixpanelHealth.AUTH_FAILED) else CS.DEGRADED
    )
    return Capability(
        key="mixpanel", label="Mixpanel", state=cs,
        detail=meta.get("detail") or ("Near real-time polling" if auth_ok else auth_err),
        last_success=ok or last_sync, last_error=err or auth_err, last_error_at=err_at,
        meta={"health_state": mp_state, "probe_latency_ms": latency_ms, "sync_mode": "near_real_time_polling", **meta},
    )


async def check_crawler(session: AsyncSession) -> Capability:
    mod = adapters.optional("app.connectors.web")
    if mod is None or not hasattr(mod, "WebCollector"):
        return Capability(
            key="crawler", label="Crawler", state=CS.UNAVAILABLE, detail="web collector module not installed"
        )
    ev = adapters.optional("app.models.evidence")
    ok = err = err_at = None
    if ev is not None:
        E = ev.Evidence
        ok = (
            await session.execute(select(func.max(E.retrieved_at)).where(E.status.in_(["live", "changed"])))
        ).scalar()
        row = (
            await session.execute(
                select(E.url, E.retrieved_at)
                .where(E.status == "failed")
                .order_by(E.retrieved_at.desc().nulls_last())
                .limit(1)
            )
        ).first()
        if row and (ok is None or (row[1] and row[1] > ok)):
            err, err_at = f"last fetch failure: {row[0]}", row[1]
    return Capability(
        key="crawler",
        label="Crawler",
        state=CS.HEALTHY,
        detail="collector available",
        last_success=ok,
        last_error=err,
        last_error_at=err_at,
    )


def _version_in(directory) -> str | None:
    if not directory.is_dir():
        return None
    for name in ("metadata.json", "manifest.json", "metrics.json"):
        f = directory / name
        if f.is_file():
            try:
                data = json.loads(f.read_text())
                return str(
                    data.get("version") or data.get("artifact_version") or data.get("trained_at") or f.name
                )
            except (OSError, ValueError):
                return f.name
    files = sorted(p.name for p in directory.iterdir() if p.is_file() and not p.name.startswith("."))
    return files[0] if files else None


def _artifact_version() -> str | None:
    """Read the EvidenceRanker manifest. Also accept a manifest directly under ml/artifacts/."""
    return _version_in(ARTIFACT_DIR) or _version_in(ARTIFACT_DIR.parent)


_ranker_cache: tuple[float, Capability] | None = None
RANKER_TTL = 60.0


def _rel_path(p: Any) -> str | None:
    """Never expose absolute server filesystem paths through the API."""
    if not p:
        return None
    try:
        return str(Path(str(p)).resolve().relative_to(ROOT))
    except (ValueError, OSError):
        return Path(str(p)).name


def _probe_ranker(mod: Any) -> Capability:
    """Blocking (may load model weights); run in a thread and cache."""
    try:
        status = mod.EvidenceRanker.load().status()
    except Exception as exc:
        return Capability(
            key="ml_ranker", label="Evidence model", state=CS.UNAVAILABLE, detail="ranker failed to load",
            last_error=repr(exc), last_error_at=utcnow(),
        )  # fmt: skip
    meta = {
        "artifact_version": status.get("version"),
        "method": status.get("method"),
        "path": _rel_path(status.get("path")),
    }
    if status.get("available") and not status.get("degraded"):
        return Capability(key="ml_ranker", label="Evidence model", state=CS.HEALTHY, meta=meta)
    if status.get("available"):
        return Capability(
            key="ml_ranker", label="Evidence model", state=CS.DEGRADED,
            detail="trained artifact loaded without embeddings (lexical features only)", meta=meta,
        )  # fmt: skip
    return Capability(
        key="ml_ranker", label="Evidence model", state=CS.DEGRADED,
        detail="no trained artifact; heuristic fallback in use",
        last_error=status.get("load_error"), meta=meta,
    )  # fmt: skip


async def check_ranker() -> Capability:
    global _ranker_cache
    mod = adapters.optional("app.evidence.ranker")
    if mod is None or not hasattr(mod, "EvidenceRanker"):
        return Capability(
            key="ml_ranker",
            label="Evidence model",
            state=CS.UNAVAILABLE,
            detail="ranker module not installed",
        )
    now = time.monotonic()
    if _ranker_cache and now - _ranker_cache[0] < RANKER_TTL:
        return _ranker_cache[1]
    cap = await asyncio.to_thread(_probe_ranker, mod)
    _ranker_cache = (now, cap)
    return cap


async def latest_policy_update(session: AsyncSession) -> tuple[datetime | None, int, str | None]:
    pm = adapters.policy_models()
    if pm is None or not hasattr(pm, "PolicyVersion"):
        return None, 0, None
    PV = pm.PolicyVersion
    n = (await session.execute(select(func.count()).select_from(PV))).scalar_one()
    row = (
        await session.execute(select(PV.created_at, PV.version).order_by(PV.created_at.desc()).limit(1))
    ).first()
    return (row[0], n, row[1]) if row else (None, n, None)


async def check_policy(session: AsyncSession) -> Capability:
    mod = adapters.optional("app.policy")
    if mod is None or not hasattr(mod, "Policy"):
        return Capability(
            key="policy", label="Policy model", state=CS.UNAVAILABLE, detail="policy module not installed"
        )
    at, n, version = await latest_policy_update(session)
    if n == 0:
        return Capability(
            key="policy",
            label="Policy model",
            state=CS.DEGRADED,
            detail="cold-start priors only; no persisted policy version",
            meta={"cold_start": True},
        )
    pm = adapters.policy_models()
    learned = (
        await session.execute(select(func.coalesce(func.max(pm.PolicyVersion.n_updates), 0)))
    ).scalar_one()
    cold = learned == 0
    return Capability(
        key="policy",
        label="Policy model",
        state=CS.HEALTHY,
        detail="cold-start priors in use; no verified reward has updated the policy yet" if cold else None,
        last_success=at,
        meta={"version": version, "versions": n, "cold_start": cold, "n_updates": int(learned)},
    )


async def check_llm(probe: bool) -> Capability:
    s = get_settings()
    mod = adapters.optional("app.connectors.llm")
    if mod is not None and hasattr(mod, "llm_capability"):
        cap = Capability(**{"key": "llm", "label": "LLM provider", **mod.llm_capability().as_dict()})
        meta = {**cap.meta, "scope": "this process"}  # call tracking is per process (API vs worker)
        cap = cap.model_copy(update={"meta": meta})
    elif not s.model_configured:
        return Capability(
            key="llm", label="LLM provider", state=CS.UNAVAILABLE, detail="MODEL_API_KEY not configured"
        )
    else:
        cap = Capability(
            key="llm", label="LLM provider", state=CS.HEALTHY, detail="credentials configured",
            meta={"model": s.model_name, "protocol": s.model_protocol},
        )  # fmt: skip
    if not probe or not s.model_configured:
        return cap
    try:
        async with httpx.AsyncClient(timeout=3.0) as c:
            r = await c.get(s.model_resolved_base_url)
        meta = {**cap.meta, "endpoint_http_status": r.status_code, "endpoint_reachable": r.status_code < 500}
        return cap.model_copy(update={"meta": meta})
    except httpx.HTTPError as exc:
        meta = {**cap.meta, "endpoint_reachable": False}
        return cap.model_copy(
            update={"state": CS.UNAVAILABLE, "last_error": repr(exc), "last_error_at": utcnow(), "meta": meta}
        )


async def check_github(probe: bool, session: AsyncSession) -> Capability:
    """OPTIONAL GitHub executor. Unconfigured is a normal state (`unavailable`, detail says optional), never an
    alarm: it is reported under `executors`, not under the core `capabilities` that drive `overall`."""
    s = get_settings()
    missing = [
        n
        for n, v in (
            ("GITHUB_TOKEN", s.github_token),
            ("GITHUB_OWNER", s.github_owner),
            ("GITHUB_REPO", s.github_repo),
        )
        if not v
    ]
    meta: dict[str, Any] = {
        "optional": True,
        "available": not missing,
        "repo": f"{s.github_owner}/{s.github_repo}" if not missing else None,
    }
    label = "GitHub PR executor (optional)"
    if missing:
        return Capability(
            key="github", label=label, state=CS.UNAVAILABLE,
            detail="optional executor, not configured; the manual executor is used", meta=meta,
        )  # fmt: skip
    if not probe:
        return Capability(
            key="github", label=label, state=CS.HEALTHY,
            detail="credentials configured, not verified by a live call; used only when an operator selects it",
            meta=meta,
        )  # fmt: skip
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(
                f"https://api.github.com/repos/{s.github_owner}/{s.github_repo}",
                headers={
                    "Authorization": f"Bearer {s.github_token}",
                    "Accept": "application/vnd.github+json",
                },
            )
        if r.status_code == 200:
            return Capability(key="github", label=label, state=CS.HEALTHY, last_success=utcnow(), meta=meta)
        return Capability(
            key="github", label=label, state=CS.DEGRADED, last_error=f"HTTP {r.status_code}",
            last_error_at=utcnow(), meta=meta,
        )  # fmt: skip
    except httpx.HTTPError as exc:
        return Capability(
            key="github", label=label, state=CS.UNAVAILABLE, last_error=repr(exc), last_error_at=utcnow(), meta=meta
        )  # fmt: skip


async def check_executors(probe: bool, session: AsyncSession) -> dict[str, Capability]:
    """Executor availability. `manual` is healthy by definition (it needs nothing)."""
    from app.interventions.manual import executor_capabilities

    caps = executor_capabilities()
    out: dict[str, Capability] = {}
    for name, c in caps.items():
        if name == "github_pr":
            continue  # reported (with an optional live probe) by check_github under the key "github"
        out[name] = Capability(
            key=name, label=c.label, state=CS.HEALTHY if c.available else CS.UNAVAILABLE, detail=c.detail,
            meta={"available": c.available, "default": c.default, "mutates_external": c.mutates_external,
                  "optional": name != "manual"},
        )  # fmt: skip
    gh = await check_github(probe, session)
    out["github"] = gh
    return out


async def _guarded(key: str, label: str, coro) -> Capability:
    try:
        return await asyncio.wait_for(coro, CHECK_TIMEOUT)
    except Exception as exc:  # a failing check must never take the endpoint down
        log.warning("capability.check_failed", key=key, error=repr(exc))
        return Capability(
            key=key,
            label=label,
            state=CS.UNAVAILABLE,
            detail="check failed",
            last_error=repr(exc),
            last_error_at=utcnow(),
        )


async def check_executors_safe(probe: bool) -> dict[str, Capability]:
    try:
        async with get_sessionmaker()() as s:
            return await asyncio.wait_for(check_executors(probe, s), CHECK_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 - executor reporting must never take the endpoint down
        log.warning("capability.executors_failed", error=repr(exc))
        return {"manual": Capability(key="manual", label="Manual", state=CS.HEALTHY,
                                     detail="always available", meta={"available": True, "default": True})}


async def graph_block() -> dict[str, Any]:
    """Neo4j projection state + detected capabilities. Informational: never feeds `overall`."""
    try:
        from app.graph.client import get_graph_client

        c = get_graph_client()
        health = await asyncio.wait_for(c.health(), CHECK_TIMEOUT)
        detected = await asyncio.wait_for(c.capabilities(), CHECK_TIMEOUT) if health["state"] == "READY" else {
            "configured": c.configured}
        return {"state": health["state"], "latency_ms": health.get("latency_ms"), "error": health.get("error"),
                "database": health.get("database"), **{k: v for k, v in detected.items() if k != "error"}}
    except Exception as exc:  # noqa: BLE001
        log.warning("capability.graph_failed", error=type(exc).__name__)
        return {"state": "DEGRADED", "error": type(exc).__name__}


async def collect_capabilities(session: AsyncSession, probe: bool = False) -> CapabilitiesOut:
    """DB-backed checks share one session sequentially; network checks run concurrently on their own."""

    async def with_session(fn, *args):
        async with get_sessionmaker()() as s:
            return await fn(s, *args)

    executors = await check_executors_safe(probe)
    caps = await asyncio.gather(
        _guarded("database", "Database", with_session(check_database)),
        _guarded("redis", "Redis", check_redis()),
        _guarded("workers", "Workers", with_session(check_workers)),
        _guarded("profound", "Profound", with_session(check_profound)),
        _guarded("mixpanel", "Mixpanel", with_session(check_mixpanel)),
        _guarded("crawler", "Crawler", with_session(check_crawler)),
        _guarded("ml_ranker", "Evidence model", check_ranker()),
        _guarded("policy", "Policy model", with_session(check_policy)),
        _guarded("llm", "LLM provider", check_llm(probe)),
    )
    by_key = {c.key: c for c in caps}
    by_key["api"] = Capability(key="api", label="API", state=CS.HEALTHY, last_success=utcnow())
    ordered = {k: by_key[k] for k in ("api", *(c.key for c in caps))}
    states = {c.state for c in ordered.values()}
    if by_key["database"].state == CS.UNAVAILABLE:
        overall = CS.UNAVAILABLE
    elif states == {CS.HEALTHY}:
        overall = CS.HEALTHY
    else:
        overall = CS.DEGRADED
    try:
        ingest, _, _ = await _job_history(session, INGEST_KINDS)
        invest, _, _ = await _job_history(session, ("investigate_incident",))
        policy_at, _, _ = await latest_policy_update(session)
    except Exception as exc:
        log.warning("capability.history_failed", error=repr(exc))
        ingest = invest = policy_at = None
    from app.control_policy.learner import ALGORITHM, POLICY_VERSION
    from app.core.config import get_settings
    from app.intelligence.laya.health import laya_capability_block

    s = get_settings()
    laya = laya_capability_block()
    return CapabilitiesOut(
        checked_at=utcnow(),
        overall=overall,
        capabilities=ordered,
        executors=executors,
        last_ingestion=ingest,
        last_investigation=invest,
        last_policy_update=policy_at,
        model_artifact_version=_artifact_version(),
        next_ingestion=_next_ingest(),
        graph=await graph_block(),
        decision_engine={
            "mode": (s.control_policy_mode or "SHADOW").upper(),
            "policy_algorithm": ALGORITHM,
            "policy_version": POLICY_VERSION,
            "laya": laya,
        },
    )
