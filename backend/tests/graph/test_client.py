import asyncio

import pytest
import structlog
from app.api.logging import redact_secrets
from app.graph.client import GraphClient, org_params, require_org
from app.graph.errors import (
    GraphAuthFailed,
    GraphNotConfigured,
    GraphQueryError,
    GraphTimeout,
    GraphUnavailable,
    OrganizationScopeError,
    scrub,
)
from app.graph.health import graph_health
from neo4j import exceptions as nx

from tests.graph.support import PASSWORD, URI, FakeDriver, client_with, make_settings


async def test_not_configured_raises_typed_and_health_state():
    c = GraphClient(make_settings(neo4j_uri="", neo4j_password=""))
    with pytest.raises(GraphNotConfigured):
        await c.run_read("RETURN 1")
    assert (await c.health())["state"] == "NOT_CONFIGURED"
    assert (await c.capabilities())["gds_plugin"] == "not_configured"


def test_enabled_resolution():
    assert make_settings().neo4j_active is True  # auto: uri + password present
    assert make_settings(neo4j_enabled=False).neo4j_active is False
    assert make_settings(neo4j_password="").neo4j_active is False


async def test_connection_failure_is_unavailable_and_scrubbed():
    exc = nx.ServiceUnavailable(f"cannot connect to {URI} with {PASSWORD}")
    c = client_with(FakeDriver(raise_on_session=exc))
    with pytest.raises(GraphUnavailable) as ei:
        await c.run_read("RETURN 1")
    assert PASSWORD not in str(ei.value) and PASSWORD not in (c.last_error or "")
    assert (await graph_health(c, force=True))["state"] == "DEGRADED"


async def test_auth_failure():
    c = client_with(FakeDriver(raise_on_session=nx.AuthError(f"bad creds {PASSWORD}")))
    with pytest.raises(GraphAuthFailed) as ei:
        await c.run_write("CREATE (n)")
    assert PASSWORD not in str(ei.value)
    h = await graph_health(c, force=True)
    assert h["state"] == "AUTH_FAILED" and PASSWORD not in str(h)


async def test_timeout_maps_to_timeout_error():
    c = client_with(FakeDriver(raise_on_run=TimeoutError()))
    with pytest.raises(GraphTimeout):
        await c.run_read("MATCH (n) RETURN n", timeout=0.1)


async def test_client_side_timeout_backstop():
    d = FakeDriver()

    async def slow(*a, **k):
        await asyncio.sleep(5)

    c = client_with(d, neo4j_max_retry_time_s=0.0)
    d.session = lambda **kw: type("S", (), {
        "__aenter__": lambda s: _ret(s), "__aexit__": lambda s, *a: _ret(False),
        "execute_read": slow, "execute_write": slow})()
    with pytest.raises(GraphTimeout):
        await c.run_read("RETURN 1", timeout=0.05)


async def _ret(v):
    return v


async def test_query_error_is_not_an_outage():
    c = client_with(FakeDriver(raise_on_run=nx.CypherSyntaxError("Invalid input")))
    with pytest.raises(GraphQueryError):
        await c.run_read("NOPE")
    assert c.last_state != "DEGRADED"


async def test_success_updates_state_and_batch_runs_all():
    d = FakeDriver()
    c = client_with(d)
    out = await c.run_write_batch([("A", {"x": 1}), ("B", {})])
    assert len(out) == 2 and [q for q, _ in d.calls] == ["A", "B"]
    assert c.last_state == "READY"
    h = await graph_health(c, force=True)
    assert h["state"] == "READY"


def test_org_scope_helper_requires_org():
    for bad in (None, "", "  ", "None", True):
        with pytest.raises(OrganizationScopeError):
            require_org(bad)
    assert org_params("o1", {"a": 1}) == {"a": 1, "organization_id": "o1"}
    with pytest.raises(OrganizationScopeError):
        org_params("o1", {"organization_id": "o2"})


async def test_scoped_queries_require_org_and_param_reference():
    d = FakeDriver()
    c = client_with(d)
    with pytest.raises(OrganizationScopeError):
        await c.scoped_read(None, "MATCH (n {organization_id: $organization_id}) RETURN n")
    with pytest.raises(OrganizationScopeError):
        await c.scoped_read("o1", "MATCH (n) RETURN n")
    await c.scoped_read("o1", "MATCH (n {organization_id: $organization_id}) RETURN n")
    assert d.calls[-1][1]["organization_id"] == "o1"


def test_scrub_and_log_redaction():
    assert PASSWORD not in scrub(f"x {PASSWORD} y", PASSWORD)
    assert "S3cr3t" not in scrub(f"conn to {URI}")
    assert "neo4j+s://[redacted]@abc" in scrub(f"conn to {URI}")
    from app.core.config import get_settings

    s = get_settings()
    old = s.neo4j_password
    s.neo4j_password = PASSWORD
    try:
        ev = redact_secrets(None, "info", {"event": f"fail {PASSWORD}", "uri": URI, "password": PASSWORD,
                                           "nested": {"m": f"bolt://u:{PASSWORD}@h"}})
    finally:
        s.neo4j_password = old
    assert PASSWORD not in str(ev)


async def test_error_logs_never_contain_secret():
    c = client_with(FakeDriver(raise_on_session=nx.ServiceUnavailable(f"{URI} {PASSWORD}")))
    with structlog.testing.capture_logs() as logs, pytest.raises(GraphUnavailable):
        await c.run_read("RETURN 1")
    assert PASSWORD not in str(logs)


async def test_close_closes_driver():
    d = FakeDriver()
    c = GraphClient(make_settings())
    c._driver = d
    await c.close()
    assert d.closed and c._driver is None
