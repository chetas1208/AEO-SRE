import pytest
from app.graph import schema
from app.graph.errors import GraphError, OrganizationScopeError

from tests.graph.support import FakeDriver, client_with


def test_statements_idempotent_form_and_cover_ids():
    stmts = schema.schema_statements()
    assert all("IF NOT EXISTS" in s for s in stmts)
    for label, prop in schema.NODE_IDS.items():
        assert any(f"(n:{label}) REQUIRE n.{prop} IS UNIQUE" in s for s in stmts)
    assert len(schema.NODE_IDS) == 18 and schema.SCHEMA_VERSION == 1


async def test_ensure_schema_runs_all_and_is_repeatable():
    d = FakeDriver(rows=[])
    c = client_with(d)
    await schema.ensure_schema(c)
    first = [q for q, _ in d.calls if q.startswith("CREATE")]
    await schema.ensure_schema(c)
    second = [q for q, _ in d.calls if q.startswith("CREATE")]
    assert len(first) == len(schema.schema_statements()) and len(second) == 2 * len(first)
    assert first == second[len(first):]


def test_merge_node_contract():
    q, p = schema.merge_node("Event", "org1", "e1", {"event_type": "X", "app": "evil", "organization_id": "evil"})
    assert "MERGE (n:Event {event_id: $id})" in q and "organization_id" not in q.split("SET")[0]
    assert p["id"] == "e1" and p["props"]["app"] == schema.APP_NAMESPACE and p["props"]["organization_id"] == "org1"
    with pytest.raises(OrganizationScopeError):
        schema.merge_node("Event", None, "e1")
    with pytest.raises(GraphError):
        schema.merge_node("Evil) DETACH DELETE (m", "o", "1")
    with pytest.raises(GraphError):
        schema.merge_node("Event", "o", "")


async def test_clear_requires_confirmation_and_only_app_namespace():
    d = FakeDriver(rows=[{"c": 3}])
    c = client_with(d)
    with pytest.raises(GraphError):
        await schema.clear_application_graph(c, confirm="yes")
    assert not d.calls
    assert await schema.clear_application_graph(c, confirm=schema.CLEAR_CONFIRMATION) == 3
    q, p = d.calls[0]
    assert "{app: $app}" in q and p["app"] == "profound-change-guard"
    with pytest.raises(GraphError):
        await schema.clear_application_graph(client_with(FakeDriver(), neo4j_database="system"),
                                             confirm=schema.CLEAR_CONFIRMATION)
