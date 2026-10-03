"""Transport adapter: raw HTTP-ish request -> auth -> rate limit -> size -> JSON -> validate -> tool -> response.

Deliberately framework-free (takes bytes + header values, returns status/body/headers) so the FastAPI route is a
three-line shim and another transport (MCP, ...) can call `dispatch` or `TOOLS` directly.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.audit import audit
from app.core.config import Settings, get_settings
from app.experiments import window as vwindow
from app.integrations.muse import errors as E
from app.integrations.muse.auth import authenticate, resolve_org
from app.integrations.muse.ratelimit import LIMITER
from app.integrations.muse.tools import ACTOR, TOOL_BY_SLUG, ToolContext, ToolSpec

log = structlog.get_logger("muse")
MAX_BODY_BYTES = 64 * 1024


@dataclass
class MuseResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)


def source_mode(header: str | None, settings: Settings) -> str:
    """LIVE unless a SIMULATED header is honoured (non-production, or MUSE_ALLOW_SIMULATED=true)."""
    v = (header or "").strip().upper()
    if not v:
        return "LIVE"
    if v not in ("LIVE", "SIMULATED"):
        raise E.InvalidRequest("X-Muse-Source-Mode must be LIVE or SIMULATED", [{"loc": ["header", "x-muse-source-mode"]}])
    if v == "SIMULATED" and (settings.environment != "production" or settings.muse_allow_simulated):
        return "SIMULATED"
    return "LIVE"


def _validation_error(spec: ToolSpec, exc: ValidationError) -> E.MuseError:
    details = [{"loc": [str(x) for x in e.get("loc", ())], "type": e.get("type"), "message": e.get("msg")}
               for e in exc.errors()]  # never echo `input`: it may hold what we reject
    types = {d["type"] for d in details}
    if "identity_field_rejected" in types:
        return E.IdentityFieldRejected("the request carries identity or personal data, which the connector never "
                                       "accepts", details)
    if "intent_expired" in types:
        return E.IntentExpired("the intent has expired", details)
    cls = E.InvalidIntentEnvelope if spec.validation_code == "INVALID_INTENT_ENVELOPE" else E.InvalidRequest
    return cls("the request body does not match the tool input schema", details)


async def _call(spec: ToolSpec, body: bytes, session: AsyncSession, org_id: uuid.UUID, mode: str,
                request_id: str | None, settings: Settings) -> dict[str, Any]:
    try:
        raw = json.loads(body.decode("utf-8")) if body.strip() else {}
    except (UnicodeDecodeError, ValueError) as exc:
        raise E.InvalidRequest("the body is not valid JSON", [{"loc": ["body"], "type": "json_invalid"}]) from exc
    if not isinstance(raw, dict):
        raise E.InvalidRequest("the body must be a JSON object", [{"loc": ["body"], "type": "object_expected"}])
    try:
        args = spec.input_model.model_validate(raw)
    except ValidationError as exc:
        raise _validation_error(spec, exc) from exc
    ctx = ToolContext(session=session, org_id=org_id, source_mode=mode, request_id=request_id, now=vwindow.now())
    try:
        out = await asyncio.wait_for(spec.handler(ctx, args), timeout=max(0.1, settings.muse_request_timeout_s))
    except TimeoutError as exc:
        raise E.ToolTimeout("the tool exceeded its time budget", {"timeout_seconds": settings.muse_request_timeout_s}) from exc
    return out.model_dump(mode="json")


async def _audit_call(session: AsyncSession, spec: ToolSpec, request_id: str | None, org_id: uuid.UUID | None,
                      status: int, code: str | None, mode: str, started: float) -> None:
    """One audit event per authenticated call. Metadata only: tool, outcome, timing; never request content."""
    try:
        await audit(session, "system", ACTOR, "muse_call", (request_id or uuid.uuid4().hex)[:64], "muse.tool.called",
                    {"tool": spec.name, "classification": spec.classification, "status": status, "error_code": code,
                     "org_id": str(org_id) if org_id else None, "source": "muse", "source_mode": mode,
                     "duration_ms": round((time.perf_counter() - started) * 1000, 1)})
        await session.commit()
    except Exception as exc:  # noqa: BLE001 - auditing must not turn a result into a failure
        await session.rollback()
        log.warning("muse.audit_failed", error=type(exc).__name__)


async def dispatch(slug: str, body: bytes, *, authorization: str | None, source_mode_header: str | None,
                   content_length: int | None, client: str, request_id: str | None, session: AsyncSession,
                   settings: Settings | None = None) -> MuseResponse:
    settings = settings or get_settings()
    spec = TOOL_BY_SLUG[slug]
    started = time.perf_counter()
    org_id: uuid.UUID | None = None
    mode = "LIVE"
    try:
        try:
            kid = authenticate(authorization, settings)
        except E.Unauthorized:
            LIMITER.hit(f"anon:{client}", settings.muse_rate_limit_per_minute)  # throttle key guessing
            raise
        LIMITER.hit(kid, settings.muse_rate_limit_per_minute)
        if (content_length or 0) > MAX_BODY_BYTES or len(body) > MAX_BODY_BYTES:
            raise E.PayloadTooLarge(f"request body exceeds {MAX_BODY_BYTES} bytes", {"max_bytes": MAX_BODY_BYTES})
        mode = source_mode(source_mode_header, settings)
        org_id = await resolve_org(session, settings)
        result = await _call(spec, body, session, org_id, mode, request_id, settings)
        if spec.classification == "write":
            await session.commit()
        else:
            await session.rollback()
        await _audit_call(session, spec, request_id, org_id, 200, None, mode, started)
        return MuseResponse(200, result)
    except E.MuseError as exc:
        await session.rollback()
        if not isinstance(exc, E.NotConfigured | E.Unauthorized | E.RateLimited):
            await _audit_call(session, spec, request_id, org_id, exc.http_status, exc.code, mode, started)
        return MuseResponse(exc.http_status, exc.body(request_id), exc.headers)
    except Exception as exc:  # noqa: BLE001
        await session.rollback()
        known = _classify_dependency(exc)
        if known is not None:
            log.warning("muse.dependency_error", tool=spec.name, error=type(exc).__name__)
            return MuseResponse(known.http_status, known.body(request_id))
        log.error("muse.unhandled", tool=spec.name, error=type(exc).__name__)  # no message: may echo input
        err = E.Internal("internal server error")
        return MuseResponse(err.http_status, err.body(request_id))


def _classify_dependency(exc: Exception) -> E.MuseError | None:
    try:
        from sqlalchemy.exc import InterfaceError, OperationalError
        from sqlalchemy.exc import TimeoutError as PoolTimeout

        if isinstance(exc, OperationalError | InterfaceError | PoolTimeout):
            return E.Unavailable("database is unavailable; retry shortly")
    except ImportError:  # pragma: no cover
        pass
    return None
