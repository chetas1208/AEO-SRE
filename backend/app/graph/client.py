"""Async Neo4j client: one long-lived driver per process, bounded timeouts, transient-only retries.

Stable interface for the projector / query / API layers (see docs/notes/handoff-n1.md):

    client = get_graph_client()
    await client.run_read(query, params, timeout=5)                       # -> list[dict]
    await client.run_write(query, params, timeout=5)                      # -> list[dict]
    await client.run_write_batch([(query, params), ...], timeout=30)      # one transaction
    await client.scoped_read(organization_id, query, params)              # query MUST use $organization_id
    await client.health(); await client.capabilities()

Neo4j is a rebuildable projection: every failure surfaces as GraphUnavailable (or a subclass) and callers degrade.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping, Sequence
from typing import Any

import structlog
from neo4j import AsyncDriver, AsyncGraphDatabase, unit_of_work
from neo4j import exceptions as nx

from app.core.config import Settings, get_settings
from app.graph.errors import (
    GraphAuthFailed,
    GraphError,
    GraphNotConfigured,
    GraphQueryError,
    GraphTimeout,
    GraphUnavailable,
    OrganizationScopeError,
    scrub,
)

log = structlog.get_logger()

Statement = tuple[str, Mapping[str, Any]]
_AUTH_CODES = ("Neo.ClientError.Security.Unauthorized", "Neo.ClientError.Security.AuthenticationRateLimit",
               "Neo.ClientError.Security.CredentialsExpired")


def require_org(organization_id: Any) -> str:
    """Every graph query function must call this. Returns the canonical string id or raises."""
    if organization_id is None or isinstance(organization_id, bool):
        raise OrganizationScopeError("organization_id is required for every graph query")
    value = str(organization_id).strip()
    if not value or value.lower() in {"none", "null"}:
        raise OrganizationScopeError("organization_id is required for every graph query")
    return value


def org_params(organization_id: Any, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Params with a validated `organization_id`; refuses a conflicting value smuggled in `params`."""
    org = require_org(organization_id)
    out = dict(params or {})
    if "organization_id" in out and str(out["organization_id"]) != org:
        raise OrganizationScopeError("params.organization_id conflicts with the scoped organization")
    out["organization_id"] = org
    return out


def _check_scoped_query(query: str) -> None:
    if "$organization_id" not in query:
        raise OrganizationScopeError("scoped query must filter on $organization_id")


class GraphClient:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings
        self._driver: AsyncDriver | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._lock: asyncio.Lock | None = None
        self.last_state: str | None = None
        self.last_error: str | None = None
        self.last_success_at: float | None = None
        self.last_latency_ms: float | None = None
        self._health_cache: tuple[float, dict[str, Any]] | None = None
        self._caps_cache: dict[str, Any] | None = None

    # -- configuration ---------------------------------------------------------------------------------
    @property
    def settings(self) -> Settings:
        return self._settings or get_settings()

    @property
    def configured(self) -> bool:
        return self.settings.neo4j_active

    @property
    def database(self) -> str | None:
        return self.settings.neo4j_database or None

    def _scrub(self, text: Any) -> str:
        s = self.settings
        return scrub(str(text), s.neo4j_password, s.neo4j_aura_api_client_secret)

    def _fail(self, exc: BaseException) -> GraphError:
        """Map any driver/OS exception to a typed, scrubbed error and record it."""
        if isinstance(exc, GraphError):
            return exc
        msg = self._scrub(f"{type(exc).__name__}: {exc}")
        code = getattr(exc, "code", None) or ""
        if isinstance(exc, nx.AuthError) or code in _AUTH_CODES:
            err: GraphError = GraphAuthFailed("neo4j authentication failed")
        elif isinstance(exc, TimeoutError | asyncio.TimeoutError) or code.endswith("TransactionTimedOut"):
            err = GraphTimeout(f"neo4j query timed out ({type(exc).__name__})")
        elif (isinstance(exc, nx.ClientError) and not code.startswith("Neo.ClientError.Security")) or code.startswith(
            "Neo.DatabaseError.Statement."
        ):
            err = GraphQueryError(msg, code=code or None)
        elif isinstance(exc, nx.Neo4jError | nx.DriverError | OSError):
            err = GraphUnavailable(f"neo4j unavailable: {type(exc).__name__}")
        else:
            err = GraphUnavailable(f"neo4j unavailable: {type(exc).__name__}")
        if isinstance(err, GraphUnavailable):
            self.last_state = err.state
            self.last_error = self._scrub(str(err))
        log.warning("graph.error", kind=type(err).__name__, error=self._scrub(str(err)))
        return err

    # -- driver lifecycle ------------------------------------------------------------------------------
    async def _get_driver(self) -> AsyncDriver:
        s = self.settings
        if not s.neo4j_active:
            raise GraphNotConfigured("neo4j is not configured (NEO4J_URI/NEO4J_PASSWORD) or disabled")
        loop = asyncio.get_running_loop()
        if self._driver is not None and self._loop is loop:
            return self._driver
        if self._driver is not None:  # event loop changed (tests / reload): old driver cannot be reused
            self._driver = None
        try:
            self._driver = AsyncGraphDatabase.driver(
                s.neo4j_uri,
                auth=(s.neo4j_username, s.neo4j_password),
                max_connection_pool_size=s.neo4j_max_pool_size,
                connection_timeout=s.neo4j_connect_timeout_s,
                connection_acquisition_timeout=s.neo4j_acquisition_timeout_s,
                max_transaction_retry_time=s.neo4j_max_retry_time_s,
                max_connection_lifetime=1800,
                liveness_check_timeout=60,
                user_agent="profound-change-guard",
            )
        except Exception as exc:  # noqa: BLE001 - bad URI scheme etc.
            raise self._fail(exc) from None
        self._loop = loop
        return self._driver

    async def verify(self, timeout: float | None = None) -> None:
        """Open a connection and authenticate. Raises a typed GraphError; never leaks credentials."""
        t = timeout or self.settings.neo4j_connect_timeout_s + 2
        driver = await self._get_driver()
        try:
            await asyncio.wait_for(driver.verify_connectivity(), t)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from None

    async def close(self) -> None:
        driver, self._driver = self._driver, None
        self._health_cache = None
        if driver is not None:
            try:
                await asyncio.wait_for(driver.close(), 5)
            except Exception as exc:  # noqa: BLE001
                log.warning("graph.close_failed", error=self._scrub(repr(exc)))

    # -- queries ---------------------------------------------------------------------------------------
    def _timeout(self, timeout: float | None) -> float:
        return float(timeout if timeout is not None else self.settings.neo4j_query_timeout_s)

    async def _execute(self, write: bool, work, timeout: float):
        driver = await self._get_driver()
        t0 = time.perf_counter()
        try:
            async with driver.session(database=self.database) as session:
                fn = session.execute_write if write else session.execute_read
                # tx timeout is enforced server-side; wait_for is the client-side backstop (incl. retries)
                result = await asyncio.wait_for(fn(work), timeout + self.settings.neo4j_max_retry_time_s + 1)
        except Exception as exc:  # noqa: BLE001
            raise self._fail(exc) from None
        self.last_latency_ms = round((time.perf_counter() - t0) * 1000, 1)
        self.last_state, self.last_error, self.last_success_at = "READY", None, time.time()
        return result

    async def run_read(self, query: str, params: Mapping[str, Any] | None = None, *,
                       timeout: float | None = None) -> list[dict[str, Any]]:
        t = self._timeout(timeout)

        @unit_of_work(timeout=t)
        async def work(tx):
            res = await tx.run(query, dict(params or {}))
            return await res.data()

        return await self._execute(False, work, t)

    async def run_write(self, query: str, params: Mapping[str, Any] | None = None, *,
                        timeout: float | None = None) -> list[dict[str, Any]]:
        """Write in a managed transaction (retried on transient errors: statements MUST be idempotent MERGEs)."""
        t = self._timeout(timeout)

        @unit_of_work(timeout=t)
        async def work(tx):
            res = await tx.run(query, dict(params or {}))
            return await res.data()

        return await self._execute(True, work, t)

    async def run_write_batch(self, statements: Sequence[Statement], *,
                              timeout: float | None = None) -> list[list[dict[str, Any]]]:
        """All statements in ONE transaction (atomic). Returns one result list per statement."""
        t = self._timeout(timeout)
        stmts = [(q, dict(p or {})) for q, p in statements]

        @unit_of_work(timeout=t)
        async def work(tx):
            out = []
            for q, p in stmts:
                res = await tx.run(q, p)
                out.append(await res.data())
            return out

        return await self._execute(True, work, t)

    async def scoped_read(self, organization_id: Any, query: str, params: Mapping[str, Any] | None = None, *,
                          timeout: float | None = None) -> list[dict[str, Any]]:
        _check_scoped_query(query)
        return await self.run_read(query, org_params(organization_id, params), timeout=timeout)

    async def scoped_write(self, organization_id: Any, query: str, params: Mapping[str, Any] | None = None, *,
                           timeout: float | None = None) -> list[dict[str, Any]]:
        _check_scoped_query(query)
        return await self.run_write(query, org_params(organization_id, params), timeout=timeout)

    # -- health / capabilities (thin wrappers; logic in health.py / capabilities.py) ---------------------
    async def health(self, *, force: bool = False) -> dict[str, Any]:
        from app.graph.health import graph_health

        return await graph_health(self, force=force)

    async def capabilities(self, *, force: bool = False) -> dict[str, Any]:
        from app.graph.capabilities import detect_capabilities

        return await detect_capabilities(self, force=force)


_client: GraphClient | None = None


def get_graph_client() -> GraphClient:
    global _client
    if _client is None:
        _client = GraphClient()
    return _client


async def close_graph_client() -> None:
    global _client
    c, _client = _client, None
    if c is not None:
        await c.close()


async def startup_graph() -> str:
    """App/worker startup hook: verify connectivity separately; NEVER raises. Returns the graph state."""
    c = get_graph_client()
    if not c.configured:
        log.info("graph.startup", state="NOT_CONFIGURED")
        return "NOT_CONFIGURED"
    try:
        await c.verify()
        c.last_state, c.last_error = "READY", None
    except GraphUnavailable as exc:
        log.warning("graph.startup_degraded", state=exc.state, error=str(exc))
        return exc.state
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.startup_degraded", error=c._scrub(repr(exc)))
        return "DEGRADED"
    log.info("graph.startup", state="READY", database=c.database)
    return "READY"

