"""FastAPI application factory. Run: cd backend && uvicorn app.api.main:app --port 8000"""

import json
import re
import time
import uuid
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api.errors import Unavailable, register_error_handlers
from app.api.logging import configure_logging
from app.api.routes import (
    campaigns,
    canonical_claims,
    change_checks,
    control_plane,
    control_policy,
    events,
    experiments,
    graph,
    health,
    incidents,
    interventions,
    muse,
    organizations,
    policy,
    settings,
    system,
)
from app.core.config import get_settings
from app.core.db import get_engine
from app.core.events import get_event_bus
from app.core.queue import QueueUnavailable
from app.schemas.common import ErrorEnvelope

log = structlog.get_logger()


_PATH_IDS = re.compile(r"^/api/(incidents|experiments|interventions|organizations)/([0-9a-fA-F-]{36})(?:/|$)")
_CONTEXT_KEY = {"incidents": "incident_id", "experiments": "experiment_id", "interventions": "intervention_id",
                "organizations": "organization_id"}


def _path_context(scope: Scope) -> dict[str, str]:
    """Correlation ids for every log line of this request, read from the URL (never from the body)."""
    ctx: dict[str, str] = {}
    m = _PATH_IDS.match(scope.get("path", ""))
    if m:
        ctx[_CONTEXT_KEY[m.group(1)]] = m.group(2)
    for k, v in parse_qsl(scope.get("query_string", b"").decode("latin-1")):
        if k == "org_id" and len(v) == 36:
            ctx["organization_id"] = v
    return ctx


class RequestContextMiddleware:
    """Pure ASGI (safe for SSE): request id header + one structured access log line per request."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = (
            next((v.decode() for k, v in scope["headers"] if k == b"x-request-id"), None)
            or uuid.uuid4().hex[:16]
        )
        scope.setdefault("state", {})["request_id"] = request_id
        structlog.contextvars.bind_contextvars(request_id=request_id, **_path_context(scope))
        started = time.perf_counter()
        status = {"code": 0}

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                status["code"] = message["status"]
                message.setdefault("headers", []).append((b"x-request-id", request_id.encode()))
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            if not scope["path"].endswith("/events"):  # SSE access lines are logged at stream end only
                log.info(
                    "http.request", method=scope["method"], path=scope["path"], status=status["code"],
                    duration_ms=round((time.perf_counter() - started) * 1000, 1),
                )  # fmt: skip
            structlog.contextvars.clear_contextvars()


MAX_BODY_BYTES = 1_048_576  # 1 MiB: every mutation body here is a note or a small JSON change


class BodyLimitMiddleware:
    """Pure ASGI: refuse oversized request bodies with a structured 413 before any parsing (declared or streamed)."""

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        declared = next((v for k, v in scope["headers"] if k == b"content-length"), None)
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            await self._reject(scope, send)
            return
        seen = {"n": 0}
        sent = {"flag": False}

        async def counting_receive() -> Message:
            msg = await receive()
            if msg["type"] == "http.request":
                seen["n"] += len(msg.get("body", b""))
                if seen["n"] > self.max_bytes:
                    raise _TooLarge
            return msg

        async def guarded_send(message: Message) -> None:
            sent["flag"] = sent["flag"] or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except _TooLarge:
            if not sent["flag"]:
                await self._reject(scope, send)

    async def _reject(self, scope: Scope, send: Send) -> None:
        rid = scope.get("state", {}).get("request_id")
        body = json.dumps({"error": {
            "code": "PAYLOAD_TOO_LARGE", "type": "payload_too_large",
            "message": f"request body exceeds {self.max_bytes} bytes", "details": {"max_bytes": self.max_bytes},
            "request_id": rid}}).encode()
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _TooLarge(Exception):
    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    configure_logging(s.log_level, json_logs=s.environment == "production")
    log.info("api.startup", environment=s.environment)
    from app.graph import close_graph_client, startup_graph

    await startup_graph()  # verifies Neo4j separately; never raises (Postgres is the system of record)
    yield
    await close_graph_client()
    await get_event_bus().close()
    try:
        from app.workers.queue import close_pool

        await close_pool()
    except ImportError:
        pass
    await get_engine().dispose()
    log.info("api.shutdown")


ERROR_RESPONSES: dict[int | str, dict] = {
    code: {"model": ErrorEnvelope, "description": "Structured error: {error:{code,type,message,details,request_id}}"}
    for code in (400, 404, 409, 413, 422, 500, 501, 503)
}


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(
        title="AEO SRE API",
        version="0.1.0",
        description="Incident-response control plane for AI discovery. JSON is snake_case.",
        lifespan=lifespan,
    )
    origins = {s.app_base_url.rstrip("/")}
    if s.cors_allowed_origins:
        for orig in s.cors_allowed_origins.split(","):
            if orig.strip():
                origins.add(orig.strip().rstrip("/"))
    if s.environment != "production":
        origins |= {"http://localhost:3000", "http://127.0.0.1:3000"}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_origin_regex=r"^https://.*\.vercel\.app$",
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )
    app.add_middleware(BodyLimitMiddleware)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    @app.exception_handler(QueueUnavailable)
    async def _queue_unavailable(request, exc: QueueUnavailable):
        return await _handle(request, Unavailable(f"job queue unavailable: {exc}"))

    async def _handle(request, exc):
        from fastapi.responses import JSONResponse

        return JSONResponse(
            {"error": {"code": "QUEUE_UNAVAILABLE", "type": exc.error_type, "message": exc.message, "details": exc.details,
                       "request_id": getattr(request.state, "request_id", None)}},
            status_code=exc.status_code,
        )  # fmt: skip

    for module in (
        campaigns,
        control_plane,
        events,
        health,
        system,
        organizations,
        canonical_claims,
        change_checks,
        control_policy,
        incidents,
        interventions,
        experiments,
        graph,
        policy,
        settings,
    ):
        app.include_router(module.router, responses=ERROR_RESPONSES)
    app.include_router(muse.router)  # Muse connector: its own error envelope, bearer key + one org
    return app


app = create_app()
