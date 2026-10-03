from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.graph.client import GraphClient

PASSWORD = "mock-aura-test-pass"
URI = "neo4j+s://test-mock.databases.neo4j.io"


def make_settings(**kw: Any) -> Settings:
    base = {"neo4j_uri": URI, "neo4j_password": PASSWORD, "neo4j_database": "abc", "neo4j_enabled": None}
    base.update(kw)
    return Settings(_env_file=None, **base)


class FakeTx:
    def __init__(self, driver: FakeDriver):
        self.d = driver

    async def run(self, query, params=None):
        self.d.calls.append((str(query), params))
        if self.d.raise_on_run:
            raise self.d.raise_on_run
        rows = self.d.rows

        class R:
            async def data(self_inner):
                return rows

        return R()


class FakeSession:
    def __init__(self, driver: FakeDriver):
        self.d = driver

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _exec(self, work):
        if self.d.raise_on_session:
            raise self.d.raise_on_session
        return await work(FakeTx(self.d))

    execute_read = _exec
    execute_write = _exec


class FakeDriver:
    def __init__(self, rows=None, raise_on_run=None, raise_on_session=None, raise_on_verify=None):
        self.rows = rows if rows is not None else [{"ok": 1}]
        self.raise_on_run, self.raise_on_session, self.raise_on_verify = raise_on_run, raise_on_session, raise_on_verify
        self.calls: list[tuple[str, Any]] = []
        self.closed = False

    def session(self, **kw):
        return FakeSession(self)

    async def verify_connectivity(self):
        if self.raise_on_verify:
            raise self.raise_on_verify

    async def close(self):
        self.closed = True


def client_with(driver: FakeDriver, **settings_kw: Any) -> GraphClient:
    c = GraphClient(make_settings(**settings_kw))

    async def _get():
        import asyncio

        c._loop = asyncio.get_running_loop()
        return driver

    c._get_driver = _get  # type: ignore[method-assign]
    return c
