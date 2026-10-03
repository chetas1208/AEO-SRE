"""Shared fixtures. Real Postgres test DB, one per pytest process (`aeo_test_<pid>`, created on start, dropped on exit,
stale ones from dead pids are reaped). `AEO_TEST_DB_NAME=aeo_test` pins a shared name; `AEO_TEST_DB=sqlite`
(or Postgres being unreachable) selects the aiosqlite fallback.

Isolation: every test gets a clean DB (TRUNCATE ... RESTART IDENTITY after each test that touched the DB).
App-level sessions (get_sessionmaker/get_session) are pointed at the same test DB, so code under test that opens
its own sessions (SSE, workers, services) sees the same data. External HTTP is mocked with respx; no real network.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import AsyncIterator

# --- environment must be pinned BEFORE any `app.*` import (get_settings is lru_cached) ---------------------
_ADMIN_URL = "postgresql://aeo:aeo@localhost:5432/aeo"
# One database per pytest process (aeo_test_<pid>) so concurrent runs by several agents never collide.
# Set AEO_TEST_DB_NAME=aeo_test to use the shared fixed name instead.
_TEST_DB = os.environ.get("AEO_TEST_DB_NAME") or f"aeo_test_{os.getpid()}"
_PG_URL = f"postgresql+psycopg://aeo:aeo@localhost:5432/{_TEST_DB}"


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _drop_stale(c) -> None:
    """Drop aeo_test_<pid> databases left behind by crashed runs (owning pid no longer alive)."""
    for (name,) in c.execute("select datname from pg_database where datname like 'aeo\\_test\\_%'").fetchall():
        suffix = name.rsplit("_", 1)[-1]
        if suffix.isdigit() and not _pid_alive(int(suffix)) and int(suffix) != os.getpid():
            try:
                c.execute(f'drop database "{name}" with (force)')
            except Exception:
                pass


def _ensure_pg() -> bool:
    try:
        import psycopg

        with psycopg.connect(_ADMIN_URL, autocommit=True, connect_timeout=3) as c:
            _drop_stale(c)
            exists = c.execute("select 1 from pg_database where datname=%s", (_TEST_DB,)).fetchone()
            if exists and not os.environ.get("AEO_TEST_DB_NAME"):
                c.execute(f'drop database "{_TEST_DB}" with (force)')  # leftover from a reused pid
                exists = None
            if not exists:
                c.execute(f'create database "{_TEST_DB}"')
        return True
    except Exception:
        return False


USE_PG = os.environ.get("AEO_TEST_DB", "").lower() != "sqlite" and _ensure_pg()
_SQLITE_FILE = os.path.join(tempfile.gettempdir(), f"aeo_test_{os.getpid()}.sqlite")
SYNC_URL = _PG_URL if USE_PG else f"sqlite:///{_SQLITE_FILE}"
ASYNC_URL = _PG_URL.replace("+psycopg", "+asyncpg") if USE_PG else f"sqlite+aiosqlite:///{_SQLITE_FILE}"

_NEO4J_BLANK = {} if os.environ.get("NEO4J_LIVE_TESTS") == "1" else {
    "NEO4J_ENABLED": "false", "NEO4J_URI": "", "NEO4J_PASSWORD": "", "NEO4J_AURA_API_CLIENT_SECRET": "",
}  # a developer .env must never make unit tests talk to the hosted Aura instance
os.environ.update(
    {
        **_NEO4J_BLANK,
        "ENVIRONMENT": "test",
        "DATABASE_URL": _PG_URL if USE_PG else ASYNC_URL,
        # never let a developer .env leak credentials into tests: external systems are unconfigured by default
        "PROFOUND_API_KEY": "",
        "PROFOUND_BASE_URL": "",
        "GITHUB_TOKEN": "",
        "GITHUB_OWNER": "",
        "GITHUB_REPO": "",
        "MODEL_API_KEY": "",
        "REDIS_URL": "redis://127.0.0.1:1/15",  # unreachable on purpose: forces the in-process bus fallback
    }
)

import app.core.db as appdb
import app.models
import pytest
import pytest_asyncio
import respx
from app.core.config import get_settings
from app.core.db import Base
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.helpers import EmitRecorder, FakeClock

get_settings.cache_clear()


def _create_schema() -> None:
    """Fresh schema from metadata (also proves create_all works from an empty DB)."""
    import importlib
    import pkgutil

    import app.models as models_pkg

    for m in pkgutil.iter_modules(models_pkg.__path__):
        importlib.import_module(f"app.models.{m.name}")
    eng = create_engine(SYNC_URL)
    try:
        if USE_PG and os.environ.get("AEO_TEST_DB_NAME"):
            Base.metadata.drop_all(eng)  # shared fixed-name DB: only drop our own tables, never the schema
        Base.metadata.create_all(eng)
    finally:
        eng.dispose()


def pytest_sessionstart(session):
    _create_schema()


def pytest_sessionfinish(session, exitstatus):
    if not USE_PG:
        if os.path.exists(_SQLITE_FILE):
            os.remove(_SQLITE_FILE)
        return
    if os.environ.get("AEO_TEST_DB_NAME"):
        return
    try:
        import psycopg

        with psycopg.connect(_ADMIN_URL, autocommit=True, connect_timeout=3) as c:
            c.execute(f'drop database if exists "{_TEST_DB}" with (force)')
    except Exception:
        pass


def _truncate_all() -> None:
    eng = create_engine(SYNC_URL)
    try:
        with eng.begin() as conn:
            if USE_PG:
                conn.execute(text("SET LOCAL synchronous_commit = off"))
            if USE_PG:  # RESTRICT FKs + the experiments<->policy_versions cycle: one TRUNCATE ... CASCADE
                names = ", ".join(f'"{t.name}"' for t in Base.metadata.sorted_tables)
                conn.execute(text(f"TRUNCATE {names} CASCADE"))
            else:
                for t in reversed(Base.metadata.sorted_tables):
                    conn.execute(t.delete())
            if USE_PG:
                for (seq,) in conn.execute(text("select sequencename from pg_sequences where schemaname='public'")).all():
                    conn.execute(text(f'ALTER SEQUENCE "{seq}" RESTART'))
    finally:
        eng.dispose()


@pytest_asyncio.fixture
async def engine():
    eng = create_async_engine(ASYNC_URL, poolclass=NullPool)
    sm = async_sessionmaker(eng, expire_on_commit=False)
    old = (appdb._engine, appdb._sessionmaker)
    appdb._engine, appdb._sessionmaker = eng, sm
    try:
        yield eng
    finally:
        appdb._engine, appdb._sessionmaker = old
        await eng.dispose()
        _truncate_all()


@pytest.fixture
def sessionmaker(engine) -> async_sessionmaker[AsyncSession]:
    return appdb.get_sessionmaker()


@pytest_asyncio.fixture
async def session(sessionmaker) -> AsyncIterator[AsyncSession]:
    async with sessionmaker() as s:
        yield s


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def emit() -> EmitRecorder:
    return EmitRecorder()


@pytest.fixture
def mock_http():
    """respx router that FAILS on any unmocked outbound request (no accidental real network)."""
    with respx.mock(assert_all_called=False, assert_all_mocked=True) as router:
        yield router


@pytest_asyncio.fixture
async def org(session):
    from tests import factories

    return await factories.make_org(session)


@pytest_asyncio.fixture
async def app_client(engine, session):
    """httpx AsyncClient against the real FastAPI app (skips if the API is not built yet)."""
    import httpx

    main = pytest.importorskip("app.api.main", reason="API not built yet")
    app = main.app
    app.dependency_overrides[appdb.get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    app.dependency_overrides.pop(appdb.get_session, None)


async def _override_get_session():
    async with appdb.get_sessionmaker()() as s:
        yield s


@pytest.fixture
def fastapi_app(engine):
    main = pytest.importorskip("app.api.main", reason="API not built yet")
    main.app.dependency_overrides[appdb.get_session] = _override_get_session
    yield main.app
    main.app.dependency_overrides.pop(appdb.get_session, None)


@pytest_asyncio.fixture
async def web_collector(mock_http):
    """WebCollector with a fake public DNS resolver, no politeness delay, in-memory cache, all HTTP via respx."""
    from app.connectors.web.cache import ResultCache
    from app.connectors.web.collector import WebCollector

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    wc = WebCollector(resolver=resolver, per_host_min_interval=0.0, timeout=5.0,
                      cache=ResultCache(use_redis=False))
    try:
        yield wc
    finally:
        await wc.aclose()


@pytest.fixture
def fast_web(monkeypatch):
    """No real DNS and no politeness sleeps for any WebCollector created inside pipeline code."""
    import app.connectors.web.collector as wc
    import app.connectors.web.ssrf as ssrf

    async def resolver(host: str) -> list[str]:
        return ["93.184.216.34"]

    async def no_wait(self, extra_delay: float = 0.0) -> None:
        return None

    monkeypatch.setattr(ssrf, "system_resolver", resolver)
    monkeypatch.setattr(wc._HostLimiter, "wait_turn", no_wait)


@pytest.fixture
def no_queue(monkeypatch):
    """Job queue unreachable: pipeline stages chain inline immediately (no 10s redis retry loop)."""
    import app.workers.queue as wq

    async def unavailable(*a, **kw):
        raise wq.QueueUnavailable("queue disabled in tests")

    monkeypatch.setattr(wq, "enqueue_job", unavailable)


@pytest.fixture
def set_env(monkeypatch):
    """set_env(GITHUB_TOKEN="x", ...) -> applies env vars and refreshes the cached Settings (restored on teardown)."""

    def _set(**values):
        for k, v in values.items():
            monkeypatch.setenv(k.upper(), str(v))
        get_settings.cache_clear()

    yield _set
    monkeypatch.undo()
    get_settings.cache_clear()


@pytest.fixture
def inline_queue(monkeypatch):
    """Jobs enqueued by the API run in-process (same code path the worker uses), results recorded on the Job row."""
    monkeypatch.setenv("AEO_QUEUE_INLINE", "1")
