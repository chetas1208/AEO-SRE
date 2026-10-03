import httpx
import pytest
from app.api.main import create_app
from app.graph import client as gc
from app.graph.errors import GraphUnavailable
from neo4j import exceptions as nx

from tests.graph.support import PASSWORD, FakeDriver, make_settings


@pytest.fixture(autouse=True)
async def _reset():
    await gc.close_graph_client()
    yield
    await gc.close_graph_client()


async def test_startup_never_raises_when_neo4j_down(monkeypatch):
    c = gc.GraphClient(make_settings())
    monkeypatch.setattr(gc, "_client", c)

    async def boom(timeout=None):
        raise GraphUnavailable("down")

    monkeypatch.setattr(c, "verify", boom)
    assert await gc.startup_graph() == "DEGRADED"


async def test_startup_auth_failed_state(monkeypatch):
    c = gc.GraphClient(make_settings())
    monkeypatch.setattr(gc, "_client", c)
    d = FakeDriver(raise_on_verify=nx.AuthError(PASSWORD))

    async def get():
        return d

    monkeypatch.setattr(c, "_get_driver", get)
    assert await gc.startup_graph() == "AUTH_FAILED"


async def test_api_health_ok_with_neo4j_down_and_not_configured(monkeypatch):
    app = create_app()
    monkeypatch.setattr(gc, "_client", gc.GraphClient(make_settings(neo4j_uri="", neo4j_password="")))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as http:
            r = await http.get("/api/health")
            assert r.json()["neo4j_state"] == "NOT_CONFIGURED"
            assert r.status_code in (200, 503) and r.json()["database"] == "healthy"
            # now a configured-but-unreachable graph
            c = gc.GraphClient(make_settings(neo4j_uri="neo4j://127.0.0.1:1", neo4j_connect_timeout_s=0.5,
                                             neo4j_acquisition_timeout_s=0.5, neo4j_max_retry_time_s=0.0))
            monkeypatch.setattr(gc, "_client", c)
            r2 = await http.get("/api/health")
            assert r2.json()["neo4j_state"] == "DEGRADED"
            assert r2.json()["database"] == "healthy"
            assert PASSWORD not in r2.text
