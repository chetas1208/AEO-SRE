"""Live Aura checks. Run only with NEO4J_LIVE_TESTS=1 (uses the root .env credentials). Cleans up after itself."""
import os
import uuid

import pytest
from app.graph import schema
from app.graph.client import GraphClient

pytestmark = pytest.mark.skipif(os.environ.get("NEO4J_LIVE_TESTS") != "1", reason="NEO4J_LIVE_TESTS=1 not set")


@pytest.fixture
async def client():
    from app.core.config import get_settings

    get_settings.cache_clear()
    c = GraphClient()
    assert c.configured
    yield c
    await c.close()


async def test_live_connect_and_roundtrip(client):
    await client.verify()
    assert (await client.run_read("RETURN 1 AS ok"))[0]["ok"] == 1
    tid = uuid.uuid4().hex
    try:
        await client.run_write("CREATE (n:LiveTestTmp {tid: $t, app: 'neo4j-live-test'})", {"t": tid})
        assert (await client.run_read("MATCH (n:LiveTestTmp {tid: $t}) RETURN count(n) AS c", {"t": tid}))[0]["c"] == 1
    finally:
        await client.run_write("MATCH (n:LiveTestTmp {tid: $t}) DETACH DELETE n", {"t": tid})
    assert (await client.run_read("MATCH (n:LiveTestTmp {tid: $t}) RETURN count(n) AS c", {"t": tid}))[0]["c"] == 0


async def test_live_ensure_schema_idempotent(client):
    a = await schema.ensure_schema(client)
    b = await schema.ensure_schema(client)
    assert a["complete"] and b["complete"] and a["constraints"] == b["constraints"] == len(schema.NODE_IDS)


async def test_live_merge_idempotent_and_clear_only_app_nodes(client):
    await schema.ensure_schema(client)
    oid = f"livetest-{uuid.uuid4().hex}"
    other = uuid.uuid4().hex
    try:
        for _ in range(2):
            q, p = schema.merge_node("Event", oid, f"ev-{oid}", {"event_type": "TEST"})
            await client.run_write(q, p)
        await client.run_write("CREATE (n:LiveTestTmp {tid: $t, app: 'someone-else'})", {"t": other})
        n = (await client.run_read("MATCH (n:Event {event_id: $i}) RETURN count(n) AS c", {"i": f"ev-{oid}"}))[0]["c"]
        assert n == 1
        assert await schema.clear_application_graph(client, confirm=schema.CLEAR_CONFIRMATION) >= 1
        left = (await client.run_read("MATCH (n:LiveTestTmp {tid: $t}) RETURN count(n) AS c", {"t": other}))[0]["c"]
        assert left == 1  # foreign-namespace node untouched
    finally:
        await client.run_write("MATCH (n:LiveTestTmp {tid: $t}) DETACH DELETE n", {"t": other})


async def test_live_capabilities(client):
    caps = await client.capabilities(force=True)
    assert caps["server_version"] and caps["gds_plugin"] in ("present", "absent")
