"""Live Aura tests on a KNOWN SMALL GRAPH (NEO4J_LIVE_TESTS=1). Unique temporary organization ids, always cleaned up."""
from __future__ import annotations

import os
from datetime import timedelta

import pytest
from app.graph import schema
from app.graph.client import GraphClient
from app.graph.queries import GraphRepository, QueryConfig
from app.graph.results import ChangeContext

from tests.graph_features.seed import A, chain, new_org, seed_main, seed_other_org

pytestmark = pytest.mark.skipif(os.environ.get("NEO4J_LIVE_TESTS") != "1", reason="NEO4J_LIVE_TESTS=1 not set")


async def _fresh(_org):  # staleness stub: the test graph is, by construction, fully projected
    return False, None


@pytest.fixture
async def client():
    from app.core.config import get_settings

    get_settings.cache_clear()
    c = GraphClient()
    assert c.configured
    await schema.ensure_schema(c)
    yield c
    await c.close()


@pytest.fixture
async def world(client):
    org_a, org_b, org_c = new_org(), new_org(), new_org()
    try:
        for seeder in (seed_main(org_a), seed_other_org(org_b), chain(org_c)):
            await client.run_write_batch(seeder.statements(), timeout=60)
        yield org_a, org_b, org_c
    finally:
        for o in (org_a, org_b, org_c):
            await client.run_write("MATCH (n) WHERE n.organization_id = $o DETACH DELETE n", {"o": o}, timeout=60)
        left = await client.run_read("MATCH (n) WHERE n.organization_id IN $o RETURN count(n) AS c", {"o": [org_a, org_b, org_c]})
        assert left[0]["c"] == 0


def _repo(client, **kw):
    from app.graph.feature_cache import FeatureCache

    return GraphRepository(client, QueryConfig(**kw), cache=FeatureCache())


async def test_exact_features_on_known_graph(client, world):
    org, _, _ = world
    repo = _repo(client)
    gf = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A, staleness=_fresh)
    assert gf.usable and gf.source == "graph" and gf.version == "graph_context_v1"
    f = gf.features
    expected = {
        "target_degree": 4, "target_recent_change_count": 3, "target_unique_agent_count": 2,
        "target_active_experiment_count": 0, "changeset_conflict_degree": 2, "duplicate_neighbor_count": 1,
        "canonical_conflict_count": 1, "prompt_overlap_count": 3, "agent_recent_conflict_rate": 1.0,
        "agent_target_history_count": 2, "shortest_path_to_active_experiment": 3, "protected_targets_touched": 0,
        "dependency_depth": 2, "pending_predecessor_count": 1, "historical_similar_context_count": 3,
        "seconds_since_last_target_change": 600, "changes_on_target_last_1h": 2, "changes_on_target_last_24h": 2,
        "agent_changes_last_1h": 2, "conflicts_last_24h": 3,
    }
    for k, v in expected.items():
        assert f[k] == pytest.approx(v), k
    assert f["historical_allow_rate"] == pytest.approx(1 / 3)
    assert f["historical_block_rate"] == pytest.approx(1 / 3)
    assert f["historical_review_rate"] == pytest.approx(1 / 3)
    assert gf.missing == []
    assert len(gf.vector) == len(gf.names) == 46
    # reproducible
    again = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A, staleness=_fresh)
    assert again.context_hash == gf.context_hash and again.vector == gf.vector


async def test_as_of_leakage(client, world):
    org, _, _ = world
    repo = _repo(client)
    before = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A - timedelta(hours=1), staleness=_fresh)
    late = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A + timedelta(minutes=90), staleness=_fresh)
    # as_of one hour BEFORE the focal change: no cs2/cs3/conflicts yet, only the old change
    assert before.features["changes_on_target_last_1h"] == 0
    assert before.features["changeset_conflict_degree"] == 0
    # cs_future (A+1h) + k_future only visible later
    assert late.features["changes_on_target_last_1h"] == 1
    assert late.features["changeset_conflict_degree"] == 3
    assert late.features["target_recent_change_count"] == 4
    at = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A, staleness=_fresh)
    assert at.features["target_recent_change_count"] == 3 and at.features["changeset_conflict_degree"] == 2


async def test_org_isolation(client, world):
    org_a, org_b, _ = world
    repo = _repo(client)
    # org B has the same target_key '/pricing' and a protected target: must never be seen from org A
    ctx = ChangeContext(target_keys=["/pricing"], agent_id=f"{org_a}-a1")
    gf = await repo.get_graph_features(org_a, context=ctx, as_of=A, staleness=_fresh)
    assert gf.features["protected_targets_touched"] == 0 and gf.features["target_degree"] == 4
    b = await repo.get_graph_features(org_b, context=ChangeContext(target_keys=["/pricing"]), as_of=A, staleness=_fresh)
    assert b.features["protected_targets_touched"] == 1 and b.features["target_degree"] == 2
    # B's changeset queried through A's scope is simply not found
    exp = await repo.get_decision_explanation(org_a, f"{org_b}-csb", as_of=A)
    assert exp.found is False
    lin = await repo.get_change_lineage(org_a, f"{org_b}-csb", as_of=A)
    assert lin.found is False and lin.nodes == []
    pairs = await repo.get_agent_conflicts(org_a, as_of=A)
    assert all(f"{org_b}" not in p.agent_a + p.agent_b for p in pairs.pairs)
    cont = await repo.get_target_contention(org_a, as_of=A)
    assert all(not r.target_id.startswith(org_b) for r in cont.rows)


async def test_decision_explanation_text(client, world):
    org, _, _ = world
    repo = _repo(client)
    exp = await repo.get_decision_explanation(org, f"{org}-cs_del", as_of=A)
    assert exp.found and exp.decision == "DELAY"
    assert exp.text == ("Change 31 was delayed because it modifies /enterprise/security, which is measured by "
                        "Experiment 12 until 20:29 UTC.")
    s = exp.statements[0]
    assert f"{org}-e1|MEASURES|{org}-t2" in s.edges and f"{org}-dd|BASED_ON|{org}-kd" in s.edges


async def test_lineage_views(client, world):
    org, _, _ = world
    repo = _repo(client)
    v = await repo.get_change_lineage(org, f"{org}-cs_del", as_of=A)
    ids = {n.id.removeprefix(org + "-") for n in v.nodes}
    assert ids == {"cs_del", "dd", "kd", "e1", "t2", "ap", "ob", "out", "ev1", "r1", "a1"}
    assert v.focus_id == f"{org}-cs_del"
    assert f"{org}-ob|PRODUCED|{org}-out" in {e.id for e in v.edges}
    assert all(e.source in {n.id for n in v.nodes} and e.target in {n.id for n in v.nodes} for e in v.edges)
    ev = await repo.get_experiment_lineage(org, f"{org}-e1", as_of=A)
    eids = {n.id.removeprefix(org + "-") for n in ev.nodes}
    assert {"e1", "t2", "kd", "cs_del", "cs_old"} <= eids
    unknown = await repo.get_change_lineage(org, "does-not-exist", as_of=A)
    assert unknown.found is False


async def test_bounded_traversal_truncates_long_chain(client, world):
    _, _, org_c = world
    repo = _repo(client)
    v = await repo.get_change_lineage(org_c, f"{org_c}-ch0", as_of=A, max_hops=99)
    assert v.max_hops == 4 and len(v.nodes) == 5  # ch0..ch4 of a 9-node chain
    v2 = await repo.get_change_lineage(org_c, f"{org_c}-ch0", as_of=A, max_hops=2)
    assert len(v2.nodes) == 3
    gf = await repo.get_graph_features(org_c, f"{org_c}-ch0", as_of=A, staleness=_fresh)
    assert gf.features["dependency_depth"] == 4  # capped at the traversal bound


async def test_agent_conflicts_contention_claims(client, world):
    org, _, _ = world
    repo = _repo(client)
    ac = await repo.get_agent_conflicts(org, as_of=A)
    assert len(ac.pairs) == 1
    p = ac.pairs[0]
    assert (p.agent_a, p.agent_b) == (f"{org}-a1", f"{org}-a2")
    assert p.conflict_count == 2 and p.conflict_types == {"DUPLICATE": 2} and p.targets == ["/pricing"]
    late = await repo.get_agent_conflicts(org, as_of=A + timedelta(hours=2))
    assert late.pairs[0].conflict_count == 3
    only = await repo.get_agent_conflicts(org, agent_id=f"{org}-a2", as_of=A)
    assert len(only.pairs) == 1
    none = await repo.get_agent_conflicts(org, agent_id="nobody", as_of=A)
    assert none.pairs == []

    tc = await repo.get_target_contention(org, as_of=A)
    rows = {r.target_id.removeprefix(org + "-"): r for r in tc.rows}
    assert [r.target_id.removeprefix(org + "-") for r in tc.rows] == ["t1", "t2", "t3"]
    assert (rows["t1"].change_count, rows["t1"].agent_count, rows["t1"].conflict_count) == (4, 2, 4)
    assert rows["t2"].active_experiment_count == 1 and rows["t2"].protected and rows["t2"].score == 9
    assert rows["t3"].active_experiment_count == 0

    cl = await repo.get_contradicted_claims(org, as_of=A)
    assert len(cl.claims) == 1
    c = cl.claims[0]
    assert c.claim_id == f"{org}-c1" and c.conflict_count == 2 and c.changeset_count == 2 and c.agent_count == 1
    assert (await repo.get_contradicted_claims(org, as_of=A, min_count=3)).claims == []


async def test_similar_contexts(client, world):
    org, _, _ = world
    repo = _repo(client)
    sc = await repo.get_similar_contexts(org, f"{org}-cs_focal", as_of=A)
    got = [(c.changeset_id.removeprefix(org + "-"), c.decision, c.score) for c in sc.contexts]
    assert got == [("cs3", "BLOCK", 0.9), ("cs2", "DELAY", 0.8), ("cs_old", "ALLOW", 0.75)]
    assert sc.allow_rate == pytest.approx(1 / 3) and sc.block_rate == pytest.approx(1 / 3)
    # the future decision (cs_future, A+1h) never appears at A
    assert all("cs_future" not in c.changeset_id for c in sc.contexts)


async def test_unprojected_changeset_is_stale(client, world):
    org, _, _ = world
    repo = _repo(client)
    gf = await repo.get_graph_features(org, "not-projected", as_of=A, staleness=_fresh)
    assert gf.stale and gf.reason == "changeset_not_projected" and not gf.unavailable


async def test_cache_and_persist_roundtrip(client, world):
    from app.graph.feature_cache import load_persisted

    org, _, _ = world
    repo = _repo(client)
    gf = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A, staleness=_fresh, persist=True)
    again = await repo.get_graph_features(org, f"{org}-cs_focal", as_of=A, staleness=_fresh)
    assert again.source == "cache" and again.vector == gf.vector
    loaded = await load_persisted(client, org, f"{org}-cs_focal", as_of=A)
    assert loaded is not None and loaded.vector == gf.vector and loaded.context_hash == gf.context_hash
    assert await load_persisted(client, org, f"{org}-cs_focal", as_of=A + timedelta(seconds=1)) is None
