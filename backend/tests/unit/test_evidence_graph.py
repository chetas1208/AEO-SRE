"""Evidence graph + provenance tests. Pure-graph tests use in-memory stand-ins (RECORDED/TEST ONLY);
the round-trip test persists real rows through the shared test DB fixtures."""
from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from app.domain.enums import EdgeType, EvidenceStatus, EvidenceType, HypothesisStatus
from app.evidence.graph import (
    GraphIntegrityError,
    build_graph,
    evidence_detail,
    hypothesis_out,
)
from app.evidence.provenance import (
    Provenance,
    content_hash,
    edge_provenance,
    evidence_snapshot,
    normalize_text,
    snapshot_drift,
    verify_snapshot,
)
from app.schemas.evidence import EvidenceItem, GraphOut, NodeClass, NodeRole

T0 = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def uid() -> uuid.UUID:
    return uuid.uuid4()


def incident(**kw):
    base = dict(
        id=uid(), number=7, title="Visibility Loss -24pp for enterprise SSO", category="visibility_drop",
        severity="high", confidence=0.7, first_observed_at=T0, detected_at=T0,
        metrics=[{"label": "Visibility", "before": 61, "after": 37, "delta": -24, "unit": "pp"}],
    )
    return SimpleNamespace(**{**base, **kw})


def ev(inc, *, type=EvidenceType.COMPETITOR, title="ev", status=EvidenceStatus.LIVE, **kw):
    base = dict(
        id=uid(), incident_id=inc.id, type=type.value, status=status.value, title=title,
        source="competitor.example", url=f"https://competitor.example/{title}",
        content_hash=content_hash(f"body of {title}"), excerpt=f"quoted text of {title}",
        retrieval_method="http_get", retrieved_at=T0, observed_at=T0, support_score=0.8,
        contradiction_score=0.05, insufficient_score=0.1, freshness_risk=0.2, confidence=0.8,
    )
    return SimpleNamespace(**{**base, **kw})


def edge(inc, src, dst, etype=EdgeType.TRIGGERED, conf=0.8, prov=True, **kw):
    s = getattr(src, "id", src)
    d = getattr(dst, "id", dst)
    p = edge_provenance(
        source="competitor.example", extract="extract", confidence=conf, retrieval_method="http_get",
        timestamp=T0, content="some content",
    ) if prov else {}
    base = dict(id=uid(), incident_id=inc.id, src_id=s, dst_id=d, edge_type=etype.value, confidence=conf,
                provenance=p)
    return SimpleNamespace(**{**base, **kw})


def hyp(inc, evidence_ids=(), **kw):
    base = dict(id=uid(), incident_id=inc.id, title="Competitor published SAML docs", summary="s",
                status=HypothesisStatus.PROPOSED.value, confidence=0.6,
                evidence_ids=[str(i) for i in evidence_ids], rationale="r", produced_by="rules", created_at=T0)
    return SimpleNamespace(**{**base, **kw})


def node_row(inc, kind, title, **kw):
    base = dict(id=uid(), incident_id=inc.id, kind=kind, title=title, source="profound", ref_type=None,
                ref_id=None, content_hash=None, excerpt=None, retrieval_method="profound_api",
                observed_at=T0, confidence=0.9, data={})
    return SimpleNamespace(**{**base, **kw})


@pytest.fixture
def scenario():
    """root -> prompt cluster -> competitor page -> citation source; our page; hypothesis."""
    inc = incident()
    cluster = node_row(inc, "prompt_cluster", "Enterprise SSO prompts")
    comp = ev(inc, title="competitor-saml")
    cite = ev(inc, type=EvidenceType.EXTERNAL, title="techradar")
    ours = ev(inc, type=EvidenceType.OWNED, title="our-security", status=EvidenceStatus.CHANGED)
    h = hyp(inc, evidence_ids=[comp.id, cite.id])
    edges = [
        edge(inc, inc.id, cluster, EdgeType.ASSOCIATED_WITH),
        edge(inc, cluster, comp, EdgeType.COMPETES_WITH),
        edge(inc, comp, cite, EdgeType.CITES),
        edge(inc, cluster, ours, EdgeType.CONTAINS_CLAIM),
    ]
    return SimpleNamespace(inc=inc, cluster=cluster, comp=comp, cite=cite, ours=ours, h=h, edges=edges)


# ---------------------------------------------------------------- provenance


def test_hash_normalizes_whitespace_and_unicode_but_not_case():
    assert content_hash("Hello \n  world") == content_hash("Hello world")
    assert content_hash("ﬁne") == content_hash("fine")  # NFKC ligature
    assert content_hash("Hello") != content_hash("hello")
    assert content_hash("   ") is None and content_hash(None) is None
    assert normalize_text("a\r\n\tb") == "a b"
    assert len(content_hash("x")) == 64


def test_provenance_records_missing_instead_of_inventing():
    p = Provenance.from_mapping({"source": "s", "confidence": 0.5})
    assert p.source == "s" and p.hash is None
    assert set(p.missing) == {"timestamp", "extract", "hash", "retrieval_method"}
    assert not p.complete
    assert Provenance.from_evidence(ev(incident())).complete


def test_snapshot_is_deterministic_detached_and_tamper_evident():
    inc = incident()
    a, b = ev(inc, title="a"), ev(inc, title="b")
    s1 = evidence_snapshot([a, b])
    s2 = evidence_snapshot([b, a])
    assert s1 == s2 and s1["count"] == 2
    assert verify_snapshot(s1)
    a.title = "mutated after snapshot"
    assert s1["items"][0]["title"] != "mutated after snapshot" or s1["items"][1]["title"] != "mutated after snapshot"
    s1["items"][0]["confidence"] = 0.01
    assert not verify_snapshot(s1)


def test_snapshot_drift_detects_changed_hash_and_missing_rows():
    inc = incident()
    a, b = ev(inc, title="a"), ev(inc, title="b")
    snap = evidence_snapshot([a, b])
    a.content_hash = content_hash("new body")
    drift = snapshot_drift(snap, [a])
    kinds = {(d["id"], d["change"]) for d in drift}
    assert (str(a.id), "content_hash") in kinds and (str(b.id), "missing") in kinds


# ---------------------------------------------------------------- graph build / validation


def test_graph_built_from_rows_only_with_root_from_incident(scenario):
    s = scenario
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster])
    assert g.root_id == str(s.inc.id)
    root = g.node(g.root_id)
    assert root.role == NodeRole.ROOT and "Visibility Loss" in root.title
    assert "61pp -> 37pp" in root.extract
    ids = {n.id for n in g.nodes()}
    assert ids == {str(x.id) for x in (s.inc, s.cluster, s.comp, s.cite, s.ours, s.h)}
    classes = {n.title: n.node_class for n in g.nodes()}
    assert classes["competitor-saml"] == NodeClass.COMPETITOR
    assert classes["techradar"] == NodeClass.EXTERNAL
    assert classes["our-security"] == NodeClass.OWNED
    assert classes["Enterprise SSO prompts"] == NodeClass.PROMPT_CLUSTER
    assert classes[s.h.title] == NodeClass.INFERENCE


def test_every_persisted_edge_and_node_carries_provenance(scenario):
    s = scenario
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster])
    out = g.serialize()
    assert not out.validation.edges_missing_provenance
    for e in out.edges:
        if not e.derived:
            assert e.provenance.source and e.provenance.hash and e.provenance.retrieval_method
            assert e.provenance.timestamp and e.provenance.extract
    for n in out.nodes:
        assert n.provenance is not None


def test_edge_missing_provenance_is_reported_not_faked(scenario):
    s = scenario
    bad = edge(s.inc, s.comp, s.cite, EdgeType.SUPPORTS, conf=None, prov=False)
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, bad], [], nodes=[s.cluster])
    out = g.serialize()
    assert str(bad.id) in out.validation.edges_missing_provenance
    e = next(x for x in out.edges if x.id == str(bad.id))
    assert e.provenance.source is None and "source" in e.provenance.missing


def test_dangling_edge_reported_and_strict_raises(scenario):
    s = scenario
    ghost = edge(s.inc, s.comp, uid(), EdgeType.CITES)
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, ghost], [], nodes=[s.cluster])
    assert [d["edge_id"] for d in g.report.dangling_edges] == [str(ghost.id)]
    assert g.serialize().counts["dangling_edges"] == 1
    with pytest.raises(GraphIntegrityError) as exc:
        build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, ghost], [], nodes=[s.cluster], strict=True)
    assert exc.value.report.dangling_edges


def test_cycle_detected_reported_and_edges_kept(scenario):
    s = scenario
    back = edge(s.inc, s.cite, s.comp, EdgeType.ASSOCIATED_WITH)
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, back], [], nodes=[s.cluster])
    assert not g.report.is_acyclic
    cyc = g.report.cycles[0]
    assert cyc[0] == cyc[-1] and {str(s.comp.id), str(s.cite.id)} <= set(cyc)
    out = g.serialize()
    assert len(out.edges) == len(s.edges) + 1  # nothing dropped
    assert sum(e.back_edge for e in out.edges) == 1
    assert out.validation.is_acyclic is False
    # layout still succeeds
    assert out.levels and len(out.topological_order) == len(out.nodes)
    with pytest.raises(GraphIntegrityError):
        build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, back], [], nodes=[s.cluster], strict=True)


def test_acyclic_graph_levels_and_topological_order(scenario):
    s = scenario
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster])
    assert g.report.is_acyclic
    out = g.serialize()
    by_id = {n.id: n for n in out.nodes}
    root, cl, comp, cite, ours = (str(x.id) for x in (s.inc, s.cluster, s.comp, s.cite, s.ours))
    assert [by_id[x].level for x in (root, cl, comp, cite)] == [0, 1, 2, 3]
    assert by_id[ours].level == 2
    assert out.levels[0] == [root]
    pos = {n: i for i, n in enumerate(out.topological_order)}
    for e in out.edges:
        assert pos[e.source] < pos[e.target]
    assert out.validation.disconnected_nodes == []


def test_unreachable_nodes_are_flagged_and_placed_below_connected_ones(scenario):
    s = scenario
    orphan = ev(s.inc, title="orphan")
    g = build_graph(s.inc, [s.comp, s.cite, s.ours, orphan], s.edges, [], nodes=[s.cluster])
    out = g.serialize()
    assert out.validation.disconnected_nodes == [str(orphan.id)]
    lv = {n.id: n.level for n in out.nodes}
    assert lv[str(orphan.id)] > max(v for k, v in lv.items() if k != str(orphan.id))


def test_persisted_root_node_replaces_incident_root_and_aliases_edges(scenario):
    s = scenario
    root = node_row(s.inc, "root", "Visibility Loss (persisted)")
    edges = [edge(s.inc, s.inc.id, s.comp, EdgeType.TRIGGERED)]  # references incident id
    g = build_graph(s.inc, [s.comp], edges, [], nodes=[root])
    assert g.root_id == str(root.id)
    assert str(s.inc.id) not in g
    assert g.g.has_edge(str(root.id), str(s.comp.id))
    assert not g.report.dangling_edges


# ---------------------------------------------------------------- conflicts & hypotheses


def test_contradicting_evidence_is_preserved_as_edge(scenario):
    s = scenario
    contra = edge(s.inc, s.ours, s.h, EdgeType.CONTRADICTS, conf=0.7)
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, contra], [s.h], nodes=[s.cluster])
    out = g.serialize()
    ce = [e for e in out.edges if e.conflict]
    assert len(ce) == 1 and ce[0].type == EdgeType.CONTRADICTS and ce[0].source == str(s.ours.id)
    assert out.counts["conflicts"] == 1
    ho = hypothesis_out(s.h, g)
    assert ho.contradicting_evidence_ids == [s.ours.id]
    assert set(ho.evidence_ids) == {s.comp.id, s.cite.id}
    assert ho.status == HypothesisStatus.PROPOSED


def test_supports_and_contradicts_between_same_pair_both_kept(scenario):
    s = scenario
    pair = [edge(s.inc, s.cite, s.h, EdgeType.SUPPORTS), edge(s.inc, s.cite, s.h, EdgeType.CONTRADICTS)]
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, *pair], [s.h], nodes=[s.cluster])
    types = sorted(e.type.value for e in g.edges() if e.source == str(s.cite.id) and e.target == str(s.h.id))
    assert types == ["contradicts", "supports"]


def test_contradiction_flagged_evidence_without_edge_warns(scenario):
    s = scenario
    s.ours.contradiction_score, s.ours.support_score = 0.9, 0.1
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [], nodes=[s.cluster])
    assert any(str(s.ours.id) in w for w in g.report.warnings)
    contra = edge(s.inc, s.ours, s.comp, EdgeType.CONTRADICTS)
    g2 = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, contra], [], nodes=[s.cluster])
    assert not any(str(s.ours.id) in w for w in g2.report.warnings)


def test_hypothesis_evidence_ids_become_derived_edges_only_when_no_persisted_edge(scenario):
    s = scenario
    explicit = edge(s.inc, s.comp, s.h, EdgeType.SUPPORTS)
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], [*s.edges, explicit], [s.h], nodes=[s.cluster])
    to_h = [e for e in g.edges() if e.target == str(s.h.id)]
    assert {e.derived for e in to_h if e.source == str(s.comp.id)} == {False}
    derived = [e for e in to_h if e.derived]
    assert [e.source for e in derived] == [str(s.cite.id)]
    assert derived[0].provenance.hash == s.cite.content_hash
    g2 = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster],
                     derive_hypothesis_edges=False)
    assert not [e for e in g2.edges() if e.target == str(s.h.id)]


def test_hypothesis_referencing_unknown_evidence_is_reported(scenario):
    s = scenario
    h = hyp(s.inc, evidence_ids=[uid()])
    g = build_graph(s.inc, [s.comp], [], [h])
    assert len(g.report.dangling_edges) == 1


# ---------------------------------------------------------------- explain_path


def test_explain_path_walks_root_to_node_with_provenance(scenario):
    s = scenario
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster])
    exp = g.explain_path(s.cite.id)
    assert exp.found
    assert [st.node_id for st in exp.steps] == [str(x.id) for x in (s.inc, s.cluster, s.comp, s.cite)]
    assert [st.via_edge_type for st in exp.steps[1:]] == [EdgeType.ASSOCIATED_WITH, EdgeType.COMPETES_WITH,
                                                          EdgeType.CITES]
    assert exp.confidence == pytest.approx(0.8**3)
    assert exp.steps[-1].hash and exp.steps[-1].extract == "extract"
    assert "techradar" in exp.text and "cites" in exp.text


def test_explain_path_reports_contradictions_and_unreachable(scenario):
    s = scenario
    contra = edge(s.inc, s.ours, s.comp, EdgeType.CONTRADICTS)
    orphan = ev(s.inc, title="orphan")
    g = build_graph(s.inc, [s.comp, s.cite, s.ours, orphan], [*s.edges, contra], [], nodes=[s.cluster])
    exp = g.explain_path(s.comp.id)
    assert [c.edge_id for c in exp.contradictions] == [str(contra.id)]
    assert "CONFLICT" in exp.text
    lost = g.explain_path(orphan.id)
    assert not lost.found and lost.reason and lost.confidence is None
    assert g.explain_path(s.inc.id).found
    with pytest.raises(KeyError):
        g.explain_path(uid())


def test_explain_path_confidence_unknown_when_edge_confidence_missing(scenario):
    s = scenario
    edges = [*s.edges[:1], edge(s.inc, s.cluster, s.comp, EdgeType.COMPETES_WITH, conf=None, prov=False)]
    g = build_graph(s.inc, [s.comp], edges, [], nodes=[s.cluster])
    exp = g.explain_path(s.comp.id)
    assert exp.confidence is None and exp.unknown_confidence_edges == 1


# ---------------------------------------------------------------- schemas


def test_serialize_roundtrips_through_pydantic_with_camel_aliases(scenario):
    s = scenario
    g = build_graph(s.inc, [s.comp, s.cite, s.ours], s.edges, [s.h], nodes=[s.cluster])
    out = g.serialize()
    snake = out.model_dump(mode="json")
    camel = out.model_dump(mode="json", by_alias=True)
    assert "root_id" in snake and "rootId" in camel
    assert "node_class" in snake["nodes"][0] and "nodeClass" in camel["nodes"][0]
    again = GraphOut.model_validate(camel)  # accepts alias on input
    assert again == out
    assert GraphOut.model_validate(snake) == out


def test_evidence_item_schema_derives_domain_and_matches_ui_fields():
    inc = incident()
    item = EvidenceItem.model_validate(ev(inc, url="https://www.competitor.example/a/b"))
    assert item.domain == "competitor.example"
    camel = item.model_dump(by_alias=True)
    for key in ("observedAt", "supportScore", "contradictionScore", "freshnessRisk", "status", "type"):
        assert key in camel


# ---------------------------------------------------------------- DB round trip


async def test_model_round_trip_and_graph_from_db_rows(session):
    from app.models.evidence import Evidence, EvidenceEdge, EvidenceNode, Hypothesis, NodeKind
    from sqlalchemy import select

    from tests.factories import make_evidence, make_hypothesis, make_incident, make_org

    org = await make_org(session)
    inc = await make_incident(session, org, metrics=[{"label": "Visibility", "before": 61, "after": 37, "delta": -24, "unit": "pp"}])
    comp = await make_evidence(session, inc, type=EvidenceType.COMPETITOR.value, title="competitor-saml")
    ours = await make_evidence(session, inc, type=EvidenceType.OWNED.value, title="our-page",
                               status=EvidenceStatus.STALE.value)
    cluster = EvidenceNode(incident_id=inc.id, kind=NodeKind.PROMPT_CLUSTER.value, title="SSO prompts",
                           source="profound", retrieval_method="profound_api", confidence=0.9)
    session.add(cluster)
    await session.flush()
    h = await make_hypothesis(session, inc, evidence_ids=[comp.id, ours.id])
    prov = edge_provenance(source="profound", extract="cluster lost share", confidence=0.8,
                           retrieval_method="profound_api", timestamp=T0, content="payload")
    session.add_all([
        EvidenceEdge(incident_id=inc.id, src_id=inc.id, dst_id=cluster.id, edge_type=EdgeType.ASSOCIATED_WITH.value,
                     confidence=0.8, provenance=prov),
        EvidenceEdge(incident_id=inc.id, src_id=cluster.id, dst_id=comp.id, edge_type=EdgeType.COMPETES_WITH.value,
                     confidence=0.7, provenance=prov),
        EvidenceEdge(incident_id=inc.id, src_id=ours.id, dst_id=h.id, edge_type=EdgeType.CONTRADICTS.value,
                     confidence=0.6, provenance=prov),
    ])
    await session.commit()

    evidence = list((await session.scalars(select(Evidence).where(Evidence.incident_id == inc.id))).all())
    edges = list((await session.scalars(select(EvidenceEdge).where(EvidenceEdge.incident_id == inc.id))).all())
    hyps = list((await session.scalars(select(Hypothesis).where(Hypothesis.incident_id == inc.id))).all())
    nodes = list((await session.scalars(select(EvidenceNode).where(EvidenceNode.incident_id == inc.id))).all())
    assert len(evidence) == 2 and len(edges) == 3 and len(hyps) == 1 and len(nodes) == 1

    g = build_graph(inc, evidence, edges, hyps, nodes=nodes)
    out = g.serialize()
    assert out.counts["nodes"] == 5 and out.validation.is_acyclic and not out.validation.dangling_edges
    assert out.counts["conflicts"] == 1
    assert {n.node_class for n in out.nodes} >= {NodeClass.PROFOUND, NodeClass.COMPETITOR, NodeClass.OWNED,
                                                 NodeClass.PROMPT_CLUSTER, NodeClass.INFERENCE}
    detail = evidence_detail(ours, g)
    assert detail.provenance.hash == ours.content_hash and len(detail.edges_out) == 1
    snap = evidence_snapshot(evidence)
    assert verify_snapshot(snap) and snap["count"] == 2
    assert not snapshot_drift(snap, evidence)
    # JSON-safe: storable in Experiment.evidence_snapshot
    import json

    json.dumps(snap)
