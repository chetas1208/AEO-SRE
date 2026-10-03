"""Typed error bodies: {"error": {"code", "type", "message", "details", "request_id"}}.

`code` is the stable machine code (UPPER_SNAKE, e.g. EXPERIMENT_NOT_VERIFIABLE_YET); `type` is the legacy lowercase
class kept for existing clients. Domain exceptions are mapped here, centrally. No stack trace or secret is ever
returned; unexpected errors become a generic INTERNAL_ERROR (details go to the structured log only).
"""

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = structlog.get_logger()


class ApiError(Exception):
    status_code = 400
    error_type = "bad_request"

    code: str | None = None

    def __init__(
        self, message: str, details=None, *, status_code: int | None = None, error_type: str | None = None,
        code: str | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.details = details
        if code is not None:
            self.code = code
        if status_code is not None:
            self.status_code = status_code
        if error_type is not None:
            self.error_type = error_type


class NotFound(ApiError):
    status_code, error_type = 404, "not_found"


class Conflict(ApiError):
    status_code, error_type = 409, "conflict"


class IllegalTransitionError(Conflict):
    error_type = "illegal_transition"
    code = "INVALID_STATE_TRANSITION"


class Unavailable(ApiError):
    status_code, error_type = 503, "unavailable"


class NotImplementedYet(ApiError):
    """Backing module has not landed or is not configured. Never fakes data."""

    status_code, error_type = 501, "not_implemented"


def _body(request: Request, error_type: str, message: str, details=None, code: str | None = None) -> dict:
    return {
        "error": {
            "code": code or error_type.upper(),
            "type": error_type,
            "message": message,
            "details": details,
            "request_id": getattr(request.state, "request_id", None),
        }
    }


def classify(exc: BaseException) -> tuple[int, str, str, str, dict | None] | None:
    """Central domain-exception -> (status, code, type, message, details). None = not a known domain error."""
    from app.domain.errors import DomainError

    if isinstance(exc, DomainError):
        return exc.http_status, exc.code, exc.error_type, exc.message, exc.details
    name = {c.__name__ for c in type(exc).__mro__}
    msg = str(exc)
    if "IllegalTransition" in name or "IllegalExperimentTransition" in name:
        return 409, "INVALID_STATE_TRANSITION", "illegal_transition", msg, None
    if "ExperimentCollision" in name:
        return 409, "ACTION_NOT_ELIGIBLE", "conflict", msg, {"reason": "experiment_collision"}
    if "SpecError" in name:
        return 422, "EXPERIMENT_SPEC_INVALID", "validation_error", msg, None
    if "AlreadyRewarded" in name:
        return 409, "DUPLICATE_REWARD", "conflict", msg, None
    if "NotRewardable" in name or "ExperimentStateError" in name:
        return 409, "EXPERIMENT_NOT_VERIFIABLE", "conflict", msg, None
    if "GraphUnavailable" in name:  # app.graph.errors (Neo4j outage family): never a 500
        return 503, "GRAPH_UNAVAILABLE", "unavailable", "graph is unavailable", {"state": getattr(exc, "state", None)}
    if "GraphQueryError" in name:
        return 503, "GRAPH_QUERY_FAILED", "unavailable", "graph query failed", None
    if "ProfoundNotConfigured" in name:
        return 503, "PROVIDER_NOT_CONFIGURED", "unavailable", "Profound is not configured", {"provider": "profound"}
    if "ProfoundError" in name:
        return 503, "PROVIDER_UNAVAILABLE", "unavailable", "Profound is unavailable", {"provider": "profound"}
    if "LLMUnavailable" in name or "LLMInvalidOutput" in name or "LLMError" in name:
        return 503, "PROVIDER_UNAVAILABLE", "unavailable", "model provider is unavailable", {"provider": "model"}
    try:
        from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
        from sqlalchemy.exc import TimeoutError as PoolTimeout

        if isinstance(exc, OperationalError | InterfaceError | PoolTimeout) or (
            isinstance(exc, DBAPIError) and exc.connection_invalidated
        ):
            return 503, "DATABASE_UNAVAILABLE", "unavailable", "database is unavailable; retry shortly", None
    except ImportError:  # pragma: no cover
        pass
    return None


def _mapped_exception_classes() -> list[type[Exception]]:
    from sqlalchemy.exc import InterfaceError, OperationalError
    from sqlalchemy.exc import TimeoutError as PoolTimeout

    from app.connectors.llm.errors import LLMError
    from app.connectors.profound.errors import ProfoundError
    from app.domain.errors import DomainError
    from app.experiments.collision import ExperimentCollision
    from app.experiments.spec import SpecError
    from app.experiments.status import IllegalExperimentTransition
    from app.experiments.verification import ExperimentStateError
    from app.incidents.state_machine import IllegalTransition
    from app.learning.ingest import IngestError

    return [ExperimentCollision, SpecError, DomainError, IllegalTransition, IllegalExperimentTransition, ExperimentStateError, IngestError,
            ProfoundError, LLMError, OperationalError, InterfaceError, PoolTimeout]


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError):
        return JSONResponse(
            _body(request, exc.error_type, exc.message, exc.details, exc.code), status_code=exc.status_code
        )

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        errors = [
            {"loc": list(e.get("loc", ())), "msg": e.get("msg"), "type": e.get("type")} for e in exc.errors()
        ]
        return JSONResponse(
            _body(request, "validation_error", "request validation failed", errors), status_code=422
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        kind = {404: "not_found", 405: "method_not_allowed", 409: "conflict", 413: "payload_too_large",
                501: "not_implemented"}.get(exc.status_code, "http_error")
        return JSONResponse(
            _body(request, kind, str(exc.detail)), status_code=exc.status_code, headers=exc.headers
        )

    async def _domain(request: Request, exc: Exception):
        known = classify(exc)
        if known is None:
            return await _unhandled(request, exc)
        status, code, kind, message, details = known
        if status >= 500:
            log.warning("api.dependency_error", path=request.url.path, code=code, error=type(exc).__name__)
        return JSONResponse(_body(request, kind, message, details, code), status_code=status)

    for cls in _mapped_exception_classes():  # registered per class so Starlette does not re-raise them
        app.add_exception_handler(cls, _domain)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        known = classify(exc)
        if known is not None:
            status, code, kind, message, details = known
            if status >= 500:
                log.warning("api.dependency_error", path=request.url.path, code=code, error=type(exc).__name__)
            return JSONResponse(_body(request, kind, message, details, code), status_code=status)
        log.exception("api.unhandled", path=request.url.path, error=repr(exc))
        return JSONResponse(_body(request, "internal_error", "internal server error"), status_code=500)
