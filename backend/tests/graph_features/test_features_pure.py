"""Pure unit tests (no Neo4j): feature math, missing flags, leakage filter, encoder order, hash, outage fallback."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from app.graph import features as F
from app.graph import similarity as S
from app.graph.queries import (
    GraphRepository,
    build_agent_pairs,
    build_claims,
    build_contention,
    build_view,
)
from app.graph.results import ChangeContext

from tests.graph.support import FakeDriver, client_with

A = datetime(2026, 10, 3, 12, 0, tzinfo=UTC)


def _facts(**kw) -> F.ContextFacts:
    t1 = F.TargetFacts(
        id="t1", key="/p", protected=True,
        changes=[
            F.ChangeFact("old", A - timedelta(days=3), "a1"),
            F.ChangeFact("mid", A - timedelta(minutes=30), "a2", [F.ConflictFact("k", "DUPLICATE", A - timedelta(minutes=5))]),
            F.ChangeFact("new", A - timedelta(minutes=10), "a1"),
            F.ChangeFact("future", A + timedelta(hours=1), "a3"),  # must be ignored
            F.ChangeFact("self", A - timedelta(minutes=1), "a1"),  # focal: excluded from its own history
        ],
        experiments=[F.ExperimentFact("e1", A - timedelta(hours=1), A + timedelta(hours=1)),
                     F.ExperimentFact("e_future", A + timedelta(minutes=1), None),
                     F.ExperimentFact("e_done", A - timedelta(hours=5), A - timedelta(hours=1))],
        neighbors=[("ChangeSet", "old", A - timedelta(days=3)), ("ChangeSet", "future", A + timedelta(hours=1)),
                   ("ChangeSet", "self", A), ("Experiment", "e1", None)])
    base = dict(organization_id="o", changeset_id="self", changeset_found=True, agent_id="a1",
                prompt_cluster_ids=["pc"], targets=[t1], path_hops=2, path_checked=True,
                own_conflicts=[F.ConflictFact("k", "DUPLICATE", A, ["mid"]), F.ConflictFact("kc", "CANONICAL_X", A, []),
                               F.ConflictFact("kf", "DUPLICATE", A + timedelta(minutes=1), ["z"])],
                agent_changes=[F.ChangeFact("old", A - timedelta(days=3), "a1", target_ids=["t1"]),
                               F.ChangeFact("new", A - timedelta(minutes=10), "a1", [F.ConflictFact("k2", "X", A - timedelta(minutes=9))], ["t1"]),
                               F.ChangeFact("f", A + timedelta(minutes=5), "a1", target_ids=["t1"])],
                prompt_overlap={"pc": [("mid", A - timedelta(days=1)), ("old", A - timedelta(days=30)), ("future", A + timedelta(hours=1))]},
                dependency_depth=2, predecessors=[("p1", "PENDING"), ("p2", "EXECUTED")],
                similar_decisions=["ALLOW", "BLOCK", "BLOCK", "DELAY"], similar_checked=True)
    base.update(kw)
    return F.ContextFacts(**base)


def test_exact_values_and_no_leakage():
    v = F.compute_features(_facts(), A)
    assert v["target_degree"] == 2  # old + e1; future/self excluded
    assert v["target_recent_change_count"] == 3  # old (3d), mid, new inside the 7d window; future/self excluded
    assert v["target_unique_agent_count"] == 2
    assert v["target_active_experiment_count"] == 1  # e1 only
    assert v["changes_on_target_last_1h"] == 2 and v["changes_on_target_last_24h"] == 2
    assert v["seconds_since_last_target_change"] == 600
    assert v["protected_targets_touched"] == 1
    assert v["changeset_conflict_degree"] == 2 and v["canonical_conflict_count"] == 1
    assert v["duplicate_neighbor_count"] == 1
    assert v["prompt_overlap_count"] == 1  # only 'mid' (7d window, <= as_of)
    assert v["agent_recent_conflict_rate"] == 0.5 and v["agent_changes_last_1h"] == 1
    assert v["agent_target_history_count"] == 2
    assert v["shortest_path_to_active_experiment"] == 2
    assert v["dependency_depth"] == 2 and v["pending_predecessor_count"] == 1
    assert v["historical_similar_context_count"] == 4
    assert (v["historical_allow_rate"], v["historical_block_rate"], v["historical_review_rate"]) == (0.25, 0.5, 0.25)
    assert v["conflicts_last_24h"] == 2  # k (via mid, also own) + kc; kf is in the future
    assert set(v) == set(F.FEATURE_NAMES)


def test_recent_window_counts_old_change():
    v = F.compute_features(_facts(), A)
    assert v["target_recent_change_count"] == 3  # old (3d), mid, new within the 7d window


def test_unknown_is_missing_not_zero():
    facts = F.ContextFacts(organization_id="o")  # nothing known
    v = F.compute_features(facts, A)
    assert all(x is None for x in v.values())
    vec = F.encode_vector(v)
    assert vec[: F.VECTOR_DIM // 2] == [0.0] * len(F.FEATURE_NAMES) and vec[F.VECTOR_DIM // 2:] == [1.0] * len(F.FEATURE_NAMES)
    # target resolved but never changed: seconds_since_last_target_change is missing, counts are real zeros
    t = F.ContextFacts(organization_id="o", targets=[F.TargetFacts("t")])
    v2 = F.compute_features(t, A)
    assert v2["seconds_since_last_target_change"] is None and v2["target_degree"] == 0
    # no similar contexts -> rates missing, count 0
    s = F.compute_features(F.ContextFacts(organization_id="o", similar_checked=True), A)
    assert s["historical_similar_context_count"] == 0 and s["historical_allow_rate"] is None
    # no experiment within the bound: known (UNREACHABLE), not missing
    p = F.compute_features(F.ContextFacts(organization_id="o", targets=[F.TargetFacts("t")], path_checked=True), A)
    assert p["shortest_path_to_active_experiment"] == F.UNREACHABLE_HOPS


def test_vector_order_dim_and_hash_stable():
    assert len(F.FEATURE_NAMES) == 23 and len(set(F.FEATURE_NAMES)) == 23
    assert F.VECTOR_NAMES[:23] == F.FEATURE_NAMES and F.VECTOR_NAMES[23] == "target_degree__missing"
    assert F.FEATURE_NAMES[0] == "target_degree" and F.FEATURE_NAMES[-1] == "conflicts_last_24h"
    v = F.compute_features(_facts(), A)
    vec1, vec2 = F.encode_vector(v), F.encode_vector(dict(reversed(list(v.items()))))
    assert vec1 == vec2 and all(0.0 <= x <= 1.0 for x in vec1)
    h1 = F.context_hash("o", A, v, {"a": 1})
    assert h1 == F.context_hash("o", A, dict(reversed(list(v.items()))), {"a": 1})
    assert h1 != F.context_hash("o2", A, v, {"a": 1})
    assert h1 != F.context_hash("o", A + timedelta(seconds=1), v, {"a": 1})
    assert len(h1) == 64


def test_leakage_filter_on_unfiltered_facts():
    v_then = F.compute_features(_facts(), A)
    v_before = F.compute_features(_facts(), A - timedelta(days=4))
    assert v_before["target_recent_change_count"] == 0 and v_then["target_recent_change_count"] == 3
    assert v_before["changeset_conflict_degree"] == 0


def test_similarity_math():
    f = S.ContextSignature(agent_type="seo", action_type="x", claim_types=frozenset({"a", "b"}),
                           known_sets=frozenset({"claim_types"}))
    c = S.ContextSignature(agent_type="seo", action_type="y", claim_types=frozenset({"a"}))
    score, parts = S.similarity(f, c)
    assert parts == {"agent_type": 1.0, "action_type": 0.0, "claim_types": 0.5}
    assert score == pytest.approx((1.0 + 0.0 + 1.5 * 0.5) / (1.0 + 2.0 + 1.5), abs=1e-6)
    assert S.jaccard([], []) == 1.0 and S.jaccard(["a"], []) == 0.0
    r = S.decision_rates([])
    assert r["allow"] is None and r["n"] == 0


def test_pure_postprocessors():
    rows = [
        {"a": "b", "b": "c", "conflict_id": "1", "conflict_type": "D", "at": "2026-10-03T00:00:00Z", "shared": ["/x"], "aff": []},
        {"a": "b", "b": "c", "conflict_id": "1", "conflict_type": "D", "at": "2026-10-03T00:00:00Z", "shared": ["/x"], "aff": []},
        {"a": "a", "b": "c", "conflict_id": "2", "conflict_type": "O", "at": "2026-10-03T01:00:00Z", "shared": [], "aff": ["/y"]},
    ]
    pairs = build_agent_pairs(rows)
    assert [(p.agent_a, p.agent_b, p.conflict_count) for p in pairs] == [("a", "c", 1), ("b", "c", 1)]
    cl = build_claims([{"claim": {"claim_id": "c1", "text": "t"}, "conflict_id": "k1", "at": None, "changes": [{"id": "x", "agent": "a"}]},
                       {"claim": {"claim_id": "c1"}, "conflict_id": "k2", "at": None, "changes": [{"id": "y", "agent": "b"}]},
                       {"claim": {"claim_id": "c2"}, "conflict_id": "k3", "at": None, "changes": []}], 2, 10)
    assert len(cl) == 1 and cl[0].agent_count == 2 and cl[0].conflict_count == 2
    ct = build_contention([{"target": {"target_id": "t", "target_key": "/t"}, "changes": [{"id": "c", "agent": "a", "conflicts": ["k"]}],
                            "affecting": ["k2"], "exps": [{"experiment_id": "e", "status": "running"}]}], A, 5)
    assert ct[0].score == 1 + 2 * 2 + 3
    view = build_view([{"focal": {"eid": "1", "labels": ["ChangeSet"], "props": {"changeset_id": "c", "organization_id": "o"}},
                        "nodes": [{"eid": "1", "labels": ["ChangeSet"], "props": {"changeset_id": "c"}},
                                  {"eid": "2", "labels": ["Decision"], "props": {"decision_id": "d"}}],
                        "rels": [{"type": "DECIDES_ON", "s": "2", "e": "1"}]}],
                      focal_label="ChangeSet", focal_id="c", generated_at=A, as_of=A, hops=4, cap=10)
    assert [e.id for e in view.edges] == ["d|DECIDES_ON|c"] and "organization_id" not in view.nodes[0].props
    assert view.focus_id == "c"


async def _fresh(_o):
    return False, None


async def _stale(_o):
    return True, "projection_lag_300s"


async def test_outage_returns_unavailable_never_raises():
    driver = FakeDriver(raise_on_session=OSError("boom"))
    repo = GraphRepository(client_with(driver))
    gf = await repo.get_graph_features("org-1", "cs-1", as_of=A, staleness=_fresh)
    assert gf.unavailable and gf.stale and gf.source == "unavailable" and gf.vector == []
    assert gf.reason.startswith("graph_unavailable") and not gf.usable


async def test_not_configured_is_unavailable():
    from app.graph.client import GraphClient

    from tests.graph.support import make_settings

    c = GraphClient(make_settings(neo4j_enabled=False))
    gf = await GraphRepository(c).get_graph_features("org-1", "cs-1", staleness=_fresh)
    assert gf.unavailable and "NOT_CONFIGURED" in gf.reason


async def test_stale_projection_skips_queries():
    driver = FakeDriver()
    gf = await GraphRepository(client_with(driver)).get_graph_features("org-1", "cs-1", as_of=A, staleness=_stale)
    assert gf.stale and gf.unavailable and gf.reason == "projection_lag_300s" and driver.calls == []


async def test_missing_org_and_empty_context_never_raise():
    repo = GraphRepository(client_with(FakeDriver()))
    assert (await repo.get_graph_features("", "cs")).reason == "organization_required"
    assert (await repo.get_graph_features(None, "cs")).unavailable
    assert (await repo.get_graph_features("org", None, staleness=_fresh)).reason == "empty_context"


async def test_every_query_is_org_scoped_and_parameterized():
    """Statements sent for a full feature computation all carry $organization_id and no literal ids."""
    driver = FakeDriver(rows=[])
    repo = GraphRepository(client_with(driver))
    await repo.get_graph_features("org-xyz", context=ChangeContext(target_keys=["/p"], agent_id="a", prompt_cluster_ids=["pc"]),
                                  as_of=A, staleness=_fresh)
    assert driver.calls
    for q, params in driver.calls:
        assert "$organization_id" in q and params["organization_id"] == "org-xyz"
        assert "org-xyz" not in q and "/p" not in q
    for qr in (repo.get_agent_conflicts("o", as_of=A), repo.get_target_contention("o", as_of=A),
               repo.get_contradicted_claims("o", as_of=A)):
        await qr
    assert all("$organization_id" in q for q, _ in driver.calls)
