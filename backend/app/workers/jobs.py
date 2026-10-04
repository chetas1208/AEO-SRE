"""Job handlers: thin adapters from a Job payload to a pipeline stage. Each returns a JSON-able result dict."""
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.services import pipeline

Handler = Callable[[AsyncSession, dict[str, Any]], Awaitable[dict[str, Any]]]


def _id(payload: dict[str, Any], key: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(payload[key]))
    except (KeyError, ValueError) as exc:
        raise pipeline.PermanentError(f"job payload missing/invalid {key!r}") from exc


async def ingest_profound_signals(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.ingest(session, _id(p, "org_id"))


async def detect_incidents(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.detect(session, _id(p, "org_id"))


async def investigate_incident(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.investigate(session, _id(p, "incident_id"))


async def fetch_external_evidence(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.stage_collect(session, _id(p, "incident_id"))


async def rank_evidence(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.stage_rank(session, _id(p, "incident_id"))


async def generate_hypotheses(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.stage_hypotheses(session, _id(p, "incident_id"))


async def score_interventions(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.propose(session, _id(p, "incident_id"))


async def execute_intervention(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    from app.services.approvals import ApprovalError

    try:
        return await pipeline.execute(session, _id(p, "intervention_id"), executor=p.get("executor"), dry_run=p.get("dry_run"))
    except ApprovalError as exc:  # no/invalid human approval can never succeed on retry
        raise pipeline.PermanentError(f"{type(exc).__name__}: {exc}") from exc


async def verify_experiment(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.verify(session, _id(p, "experiment_id"), force=bool(p.get("force")))


async def calculate_reward(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.reward(session, _id(p, "experiment_id"))


async def update_policy(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    return await pipeline.update_policy(session, _id(p, "experiment_id"))


async def detect_discovery_gaps(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    from app.discovery_gap.service import run_job

    return await run_job(session, _id(p, "org_id"))


async def profound_agent_generation(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    from app.services.profound_agents import run_generation_job

    return await run_generation_job(session, p)


async def sync_profound_agent_runs(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    from app.services.profound_agents import sync_all_live_runs

    return await sync_all_live_runs(session)


async def ingest_mixpanel_events(session: AsyncSession, p: dict[str, Any]) -> dict[str, Any]:
    from app.integrations.mixpanel.ingest import ingest_mixpanel

    mode = str(p.get("mode") or "LIVE")
    result = await ingest_mixpanel(
        session,
        org_id=_id(p, "org_id"),
        mode=mode,
        backfill_days=int(p["backfill_days"]) if p.get("backfill_days") else None,
    )
    return result.model_dump(mode="json")


HANDLERS: dict[str, Handler] = {
    "ingest_profound_signals": ingest_profound_signals,
    "detect_incidents": detect_incidents,
    "investigate_incident": investigate_incident,
    "fetch_external_evidence": fetch_external_evidence,
    "rank_evidence": rank_evidence,
    "generate_hypotheses": generate_hypotheses,
    "score_interventions": score_interventions,
    "execute_intervention": execute_intervention,
    "verify_experiment": verify_experiment,
    "calculate_reward": calculate_reward,
    "update_policy": update_policy,
    "detect_discovery_gaps": detect_discovery_gaps,
    "ingest_mixpanel_events": ingest_mixpanel_events,
    "profound_agent_generation": profound_agent_generation,
    "sync_profound_agent_runs": sync_profound_agent_runs,
}
