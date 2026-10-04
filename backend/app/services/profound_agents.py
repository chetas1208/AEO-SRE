"""Profound agent listing and background generation runs (campaign + experiment)."""

from __future__ import annotations

import asyncio
import re
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from sqlalchemy import select

from app.connectors.profound.client import ProfoundClient
from app.connectors.profound.errors import ProfoundError
from app.core.config import get_settings

log = structlog.get_logger(__name__)

LIVE_RUN_META = {"source": "PROFOUND", "source_mode": "LIVE"}

_TERMINAL = frozenset({"succeeded", "failed", "cancelled", "skipped"})
_RUNNING = frozenset({"queued", "running", "accepted"})


def map_run_state(status: Any) -> str:
    st = str(status or "running").lower()
    if st in ("succeeded", "success", "completed"):
        return "COMPLETED"
    if st in ("failed", "error"):
        return "FAILED"
    if st in ("cancelled", "skipped"):
        return "BLOCKED"
    if st in ("queued", "accepted"):
        return "WAITING"
    if st == "running":
        return "RUNNING"
    return "RUNNING"


def _with_live_meta(run: dict[str, Any]) -> dict[str, Any]:
    return {**LIVE_RUN_META, **run}


_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.I,
)

# UI slug -> tokens matched against Profound agent names (case-insensitive).
LEGACY_AGENT_HINTS: dict[str, tuple[str, ...]] = {
    "agt-citation-recovery": ("citation", "recovery"),
    "agt-claim-verifier": ("claim", "verifier", "fact"),
    "agt-competitive-copilot": ("competitive", "differentiation", "copilot"),
    "agt-token-router": ("token", "router", "api"),
}


def profound_generation_enabled() -> bool:
    s = get_settings()
    return bool(s.profound_api_key) and bool(getattr(s, "profound_agent_runs_enabled", True))


def _agent_rows(data: Any) -> list[dict[str, Any]]:
    if not isinstance(data, dict):
        return []
    raw = data.get("data")
    return [a for a in raw if isinstance(a, dict)] if isinstance(raw, list) else []


def _published(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in agents if str(a.get("status") or "").lower() == "published"]


def _runnable_agents(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    pub = _published(agents)
    if pub:
        return pub
    return [a for a in agents if str(a.get("status") or "").lower() in ("draft", "published", "unknown")]


async def list_profound_agents(*, limit: int = 100) -> dict[str, Any]:
    """Live agent catalog from Profound API (requires PROFOUND_API_KEY)."""
    if not profound_generation_enabled():
        return {
            "status": "DISABLED",
            "configured": bool(get_settings().profound_api_key),
            "agents": [],
            "message": "Set PROFOUND_API_KEY and PROFOUND_AGENT_RUNS_ENABLED=1 to enable agent generation.",
        }
    client = ProfoundClient()
    try:
        merged: dict[str, dict[str, Any]] = {}
        for statuses in (["published"], ["draft"]):
            resp = await client.list_agents(limit=limit, statuses=statuses)
            for a in _agent_rows(resp.data):
                if a.get("id"):
                    merged[str(a["id"])] = a
        rows = list(merged.values())
        normalized = [
            {
                "id": str(a.get("id")),
                "name": a.get("name") or "Unnamed agent",
                "status": a.get("status"),
                "organization_id": a.get("organization_id"),
                "created_at": a.get("created_at"),
                "source": "profound",
                **LIVE_RUN_META,
            }
            for a in rows
            if a.get("id")
        ]
        return {
            "status": "OK",
            "configured": True,
            "agents": normalized,
            "published_count": len(_published(rows)),
            "total": len(normalized),
            **LIVE_RUN_META,
        }
    except ProfoundError as exc:
        log.warning("profound.agents_list_failed", error=type(exc).__name__)
        return {
            "status": "ERROR",
            "configured": True,
            "agents": [],
            "message": type(exc).__name__,
        }
    finally:
        await client.aclose()


def _match_legacy(token: str, agents: list[dict[str, Any]]) -> str | None:
    hints = LEGACY_AGENT_HINTS.get(token)
    if not hints:
        return None
    pool = _runnable_agents(agents)
    for a in pool:
        name = str(a.get("name") or "").lower()
        if all(h in name for h in hints):
            return str(a.get("id"))
    for a in pool:
        name = str(a.get("name") or "").lower()
        if any(h in name for h in hints):
            return str(a.get("id"))
    return None


def resolve_agent_ids(requested: list[str], catalog: list[dict[str, Any]]) -> list[str]:
    """Map UI selections (UUID or legacy slugs) to Profound agent UUIDs."""
    by_id = {str(a.get("id")): a for a in catalog if a.get("id")}
    resolved: list[str] = []
    for raw in requested:
        token = (raw or "").strip()
        if not token:
            continue
        if _UUID_RE.match(token) and token in by_id:
            resolved.append(token)
            continue
        if _UUID_RE.match(token):
            resolved.append(token)
            continue
        hit = _match_legacy(token, catalog)
        if hit:
            resolved.append(hit)
    if resolved:
        return list(dict.fromkeys(resolved))
    pool = _runnable_agents(catalog)
    return [str(a["id"]) for a in pool[:5] if a.get("id")]


def build_run_inputs(scope: str, context: dict[str, Any]) -> dict[str, Any]:
    """Generic inputs bag for Profound agents (keys depend on each agent's published schema)."""
    base = {
        "scope": scope,
        "campaign_id": context.get("campaign_id"),
        "experiment_id": context.get("experiment_id"),
        "name": context.get("name"),
        "objective": context.get("objective") or context.get("hypothesis"),
        "hypothesis": context.get("hypothesis"),
        "primary_metric": context.get("primary_metric"),
        "target_url": context.get("target_url"),
        "channels": context.get("channels"),
        "notes": context.get("notes"),
    }
    return {k: v for k, v in base.items() if v not in (None, "", [])}


async def _ensure_published(client: ProfoundClient, agent_id: str, status: str | None) -> None:
    if str(status or "").lower() == "published":
        return
    try:
        await client.publish_agent(agent_id)
    except ProfoundError as exc:
        log.warning("profound.agent_publish_skipped", agent_id=agent_id, error=type(exc).__name__)


async def _fetch_run(client: ProfoundClient, agent_id: str, run_id: str) -> dict[str, Any]:
    resp = await client.get_agent_run(agent_id, run_id)
    data = resp.data if isinstance(resp.data, dict) else {}
    return _with_live_meta(
        {
            "agent_id": agent_id,
            "run_id": run_id,
            "status": data.get("status"),
            "started_at": data.get("started_at"),
            "finished_at": data.get("finished_at"),
            "error": data.get("error"),
            "outputs": data.get("outputs"),
        }
    )


async def _start_run(
    client: ProfoundClient,
    agent_id: str,
    inputs: dict[str, Any],
    *,
    agent_status: str | None = None,
) -> dict[str, Any]:
    await _ensure_published(client, agent_id, agent_status)
    resp = await client.run_agent(agent_id, inputs=inputs or None)
    data = resp.data if isinstance(resp.data, dict) else {}
    return _with_live_meta(
        {
            "agent_id": agent_id,
            "run_id": data.get("id"),
            "status": data.get("status") or "queued",
            "started_at": data.get("started_at"),
        }
    )


async def _poll_run(client: ProfoundClient, agent_id: str, run_id: str, *, attempts: int | None = None) -> dict[str, Any]:
    if attempts is None:
        attempts = max(6, int(get_settings().profound_agent_poll_attempts))
    last = _with_live_meta({"agent_id": agent_id, "run_id": run_id, "status": "running"})
    for _ in range(attempts):
        last = await _fetch_run(client, agent_id, run_id)
        st = str(last.get("status") or "").lower()
        if st in _TERMINAL:
            return last
        await asyncio.sleep(2.0)
    return last


async def sync_run_record(client: ProfoundClient, run: dict[str, Any]) -> dict[str, Any]:
    aid, rid = run.get("agent_id"), run.get("run_id")
    if not aid or not rid:
        return _with_live_meta(dict(run))
    try:
        return await _fetch_run(client, str(aid), str(rid))
    except ProfoundError:
        return _with_live_meta(dict(run))


async def run_generation_job(session, payload: dict[str, Any]) -> dict[str, Any]:
    """ARQ handler body: start Profound agent runs for a campaign or experiment."""
    scope = str(payload.get("scope") or "campaign")
    entity_id = str(payload.get("entity_id") or "")
    requested = list(payload.get("agent_ids") or [])
    context = dict(payload.get("context") or {})

    if not profound_generation_enabled():
        result = {"status": "skipped", "reason": "profound_agent_runs_disabled"}
        await _persist_generation(session, scope, entity_id, result)
        return result

    catalog_resp = await list_profound_agents(limit=100)
    catalog = catalog_resp.get("agents") or []
    agent_ids = resolve_agent_ids(requested, catalog)

    status_by_id = {str(a.get("id")): str(a.get("status") or "") for a in catalog if a.get("id")}
    name_by_id = {str(a.get("id")): str(a.get("name") or a.get("id")) for a in catalog if a.get("id")}

    if not agent_ids:
        result = {
            **LIVE_RUN_META,
            "status": "skipped",
            "reason": "no_profound_agents",
            "message": "No Profound agents in this org; create and publish agents in Profound Command Center.",
            "runs": [],
        }
        await _persist_generation(session, scope, entity_id, result)
        return result

    client = ProfoundClient()
    runs: list[dict[str, Any]] = []
    errors: list[str] = []
    try:
        from app.services.profound_agent_factory import run_inputs_for_agent

        for aid in agent_ids:
            try:
                inputs = await run_inputs_for_agent(client, aid, context)
                started = await _start_run(client, aid, inputs, agent_status=status_by_id.get(aid))
                if started.get("run_id"):
                    polled = await _poll_run(client, aid, str(started["run_id"]))
                    runs.append(_with_live_meta({**started, **polled, "agent_name": name_by_id.get(aid)}))
                else:
                    runs.append(_with_live_meta({**started, "agent_name": name_by_id.get(aid)}))
            except ProfoundError as exc:
                errors.append(f"{aid}:{type(exc).__name__}")
                runs.append(_with_live_meta({"agent_id": aid, "status": "failed", "error": type(exc).__name__}))
    finally:
        await client.aclose()

    terminal = sum(1 for r in runs if str(r.get("status") or "").lower() in _TERMINAL)
    status = "completed" if runs and not errors and terminal == len(runs) else ("partial" if runs else "failed")
    result = {
        **LIVE_RUN_META,
        "status": status,
        "scope": scope,
        "entity_id": entity_id,
        "runs": runs,
        "errors": errors,
        "updated_at": datetime.now(UTC).isoformat(),
    }
    await _persist_generation(session, scope, entity_id, result)
    await session.commit()
    return result


async def _persist_generation(session, scope: str, entity_id: str, result: dict[str, Any]) -> None:
    if scope == "campaign":
        from app.api.routes import campaigns as campaigns_mod

        campaigns_mod.patch_campaign_generation(entity_id, result)
    elif scope == "experiment":
        try:
            eid = uuid.UUID(entity_id)
        except ValueError:
            return
        await persist_experiment_generation(session, eid, result)


async def persist_experiment_generation(session, experiment_id: uuid.UUID, result: dict[str, Any]) -> None:
    from app.models.interventions import Experiment

    exp = await session.get(Experiment, experiment_id)
    if exp is None:
        return
    pc = dict(exp.proposed_change or {})
    pc["profound_generation"] = {**LIVE_RUN_META, **result}
    exp.proposed_change = pc
    tl = list(exp.timeline or [])
    tl.append(
        {
            "at": datetime.now(UTC).isoformat(),
            "actor": "system",
            "reason": f"Profound agent generation: {result.get('status')}",
            "meta": {"runs": len(result.get("runs") or [])},
        }
    )
    exp.timeline = tl
    await session.flush()


async def sync_live_runs_for_campaign(campaign_id: str) -> dict[str, Any]:
    from app.api.routes.campaigns import CAMPAIGNS_DB, _sync_custom_from_disk, patch_campaign_generation

    _sync_custom_from_disk()
    camp = next((c for c in CAMPAIGNS_DB if c.get("id") == campaign_id), None)
    if camp is None:
        return {"status": "not_found", **LIVE_RUN_META}
    gen = dict(camp.get("profound_generation") or {})
    runs = list(gen.get("runs") or [])
    if not runs:
        return {**LIVE_RUN_META, "status": "empty", "campaign_id": campaign_id}

    client = ProfoundClient()
    try:
        synced = [await sync_run_record(client, r) for r in runs if isinstance(r, dict)]
    finally:
        await client.aclose()

    still_running = any(str(r.get("status") or "").lower() in _RUNNING for r in synced)
    gen.update(
        {
            **LIVE_RUN_META,
            "runs": synced,
            "status": "running" if still_running else gen.get("status", "completed"),
            "updated_at": datetime.now(UTC).isoformat(),
        }
    )
    patch_campaign_generation(campaign_id, gen)
    return {"status": "ok", "campaign_id": campaign_id, "profound_generation": gen, **LIVE_RUN_META}


async def sync_all_live_runs(session) -> dict[str, Any]:
    from app.api.routes.campaigns import CAMPAIGNS_DB, _sync_custom_from_disk

    _sync_custom_from_disk()
    campaigns = 0
    for c in CAMPAIGNS_DB:
        gen = c.get("profound_generation") or {}
        if gen.get("runs"):
            await sync_live_runs_for_campaign(str(c["id"]))
            campaigns += 1

    experiments = 0
    from app.models.interventions import Experiment

    q = select(Experiment.id, Experiment.proposed_change).where(Experiment.proposed_change.isnot(None))
    client = ProfoundClient()
    try:
        for exp_id, pc in (await session.execute(q)).all():
            if not isinstance(pc, dict):
                continue
            gen = pc.get("profound_generation") or {}
            runs = gen.get("runs") or []
            if not runs:
                continue
            synced = [await sync_run_record(client, r) for r in runs if isinstance(r, dict)]
            gen = {**gen, **LIVE_RUN_META, "runs": synced, "updated_at": datetime.now(UTC).isoformat()}
            await persist_experiment_generation(session, exp_id, gen)
            experiments += 1
        await session.commit()
    finally:
        await client.aclose()

    return {
        **LIVE_RUN_META,
        "status": "ok",
        "campaigns_synced": campaigns,
        "experiments_synced": experiments,
    }


async def enqueue_profound_generation(
    *,
    scope: str,
    entity_id: str,
    agent_ids: list[str],
    context: dict[str, Any],
) -> uuid.UUID | None:
    if not profound_generation_enabled():
        return None
    from app.core.queue import enqueue

    try:
        return await enqueue(
            "profound_agent_generation",
            {
                "scope": scope,
                "entity_id": entity_id,
                "agent_ids": agent_ids,
                "context": context,
            },
        )
    except Exception as exc:  # noqa: BLE001 — creation must succeed even if queue is down
        log.warning("profound.generation_enqueue_failed", scope=scope, entity_id=entity_id, error=str(exc))
        return None
