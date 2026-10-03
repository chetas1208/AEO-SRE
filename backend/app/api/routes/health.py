import os
import subprocess
from functools import lru_cache

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import get_settings
from app.core.db import get_sessionmaker, utcnow
from app.domain.enums import CapabilityState as CS
from app.schemas.system import Capability, HealthOut
from app.services import capabilities as caps

router = APIRouter(tags=["health"])
VERSION = "0.1.0"


@lru_cache(maxsize=1)
def _git_sha() -> str | None:
    env = os.environ.get("GIT_SHA") or os.environ.get("AEO_GIT_SHA")
    if env:
        return env.strip()[:40] or None
    try:
        out = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], stderr=subprocess.DEVNULL, timeout=2)
        return out.decode().strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


async def _profound_state() -> str:
    """Passive: derived from the last recorded ingest per org. Never calls Profound."""
    if not get_settings().profound_api_key:
        return "NOT_CONFIGURED"
    try:
        async with get_sessionmaker()() as session:
            sync = await caps.profound_sync_status(session)
    except Exception:  # noqa: BLE001
        return "UNVERIFIED"
    # an org that no Profound category maps to (e.g. the dev fixture org) is "not mapped", not a Profound fault
    statuses = [
        str((v or {}).get("status") or "") for v in sync["orgs"].values()
        if not str((v or {}).get("error") or "").startswith("no_profound_category_owns_")
    ]
    if not statuses:
        return "UNVERIFIED"
    return "READY" if all(st == "ok" for st in statuses) else "DEGRADED"


@router.get("/api/health", response_model=HealthOut)
async def health():
    """Each dependency is probed independently and never raises. 200 while the database is up (redis down =
    `degraded`); 503 only when the database is unavailable. Unconfigured Profound / model do not degrade it."""
    try:
        async with get_sessionmaker()() as session:
            db = await caps.check_database(session)
    except Exception as exc:  # noqa: BLE001 - engine/pool failure before a query can even run
        db = Capability(key="database", label="Database", state=CS.UNAVAILABLE, last_error=type(exc).__name__)
    redis = await caps.check_redis()
    profound_state = await _profound_state()
    try:
        from app.connectors.llm.status import current_health

        model_state = current_health().state.value
    except Exception:  # noqa: BLE001 - health must never raise
        model_state = "NOT_CONFIGURED" if not get_settings().model_api_key else "DEGRADED"
    try:
        from app.graph.health import neo4j_state

        graph_state = await neo4j_state()
    except Exception:  # noqa: BLE001 - the graph is a projection: never affects overall health
        graph_state = "DEGRADED"
    ok = db.state == CS.HEALTHY
    body = HealthOut(
        status="ok" if ok and redis.state == CS.HEALTHY else ("degraded" if ok else "unavailable"),
        version=VERSION,
        time=utcnow(),
        database=db.state,
        redis=redis.state,
        profound="configured" if get_settings().profound_api_key else "not_configured",
        model_provider="configured" if get_settings().model_api_key else "not_configured",
        profound_state=profound_state,
        model_state=model_state,
        git_sha=_git_sha(),
        neo4j_state=graph_state,
    )
    return JSONResponse(body.model_dump(mode="json"), status_code=200 if ok else 503)
