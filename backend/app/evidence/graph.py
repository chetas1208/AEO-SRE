"""Per-incident evidence graph (NetworkX), built only from persisted rows.

Inputs are the Incident row plus Evidence / EvidenceEdge / Hypothesis (and optional EvidenceNode) rows.
Nothing is invented for display: the root symptom comes from a persisted root EvidenceNode or, failing
that, from the Incident row itself. Edges point along the investigative trail (symptom -> explanation);
hypotheses are sinks. Contradicting evidence is kept as `contradicts` edges and never dropped; anything
that cannot be placed (dangling edges, cycle-closing edges) is reported, not silently discarded.
"""
import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import networkx as nx

from app.domain.enums import EdgeType, EvidenceStatus, EvidenceType
from app.evidence.provenance import Provenance, _get, _jsonable, domain_of, hash_json
from app.schemas.evidence import (
    EvidenceDetail,
    GraphEdge,
    GraphNode,
    GraphOut,
    GraphValidationOut,
    HypothesisOut,
    NodeClass,
    NodeRole,
    ProvenanceOut,
)

CONTRADICTION_FLAG_THRESHOLD = 0.5
MAX_REPORTED_CYCLES = 25
_NO_HASH_STATUSES = {EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value}
_ROLE_ORDER = {NodeRole.ROOT: 0, NodeRole.PROMPT_CLUSTER: 1, NodeRole.EVIDENCE: 2,
               NodeRole.HYPOTHESIS: 3, NodeRole.EXPERIMENT: 4}
_EVIDENCE_CLASS = {
    EvidenceType.PROFOUND.value: NodeClass.PROFOUND,
    EvidenceType.OWNED.value: NodeClass.OWNED,
    EvidenceType.COMPETITOR.value: NodeClass.COMPETITOR,
    EvidenceType.EXTERNAL.value: NodeClass.EXTERNAL,
    EvidenceType.INFERENCE.value: NodeClass.INFERENCE,
}
_SYNTHETIC_CLASS = {
    "root": (NodeClass.PROFOUND, NodeRole.ROOT),
    "prompt_cluster": (NodeClass.PROMPT_CLUSTER, NodeRole.PROMPT_CLUSTER),
    "experiment": (NodeClass.EXPERIMENT, NodeRole.EXPERIMENT),
}


class GraphIntegrityError(ValueError):
    def __init__(self, message: str, report: "ValidationReport"):
        super().__init__(message)
        self.report = report


@dataclass(slots=True)
class NodeData:
    id: str
    node_class: NodeClass
    role: NodeRole
    title: str
    provenance: Provenance
    status: str | None = None
    evidence_id: Any = None
    hypothesis_id: Any = None
    source: str | None = None
    url: str | None = None
    observed_at: datetime | None = None
    confidence: float | None = None
    extract: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class EdgeData:
    id: str
    source: str
    target: str
    type: EdgeType
    provenance: Provenance
    confidence: float | None = None
    derived: bool = False
    back_edge: bool = False


@dataclass(slots=True)
class ValidationReport:
    cycles: list[list[str]] = field(default_factory=list)
    dangling_edges: list[dict[str, Any]] = field(default_factory=list)
    nodes_missing_provenance: list[str] = field(default_factory=list)
    edges_missing_provenance: list[str] = field(default_factory=list)
    disconnected_nodes: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_acyclic(self) -> bool:
        return not self.cycles

    @property
    def ok(self) -> bool:
        return self.is_acyclic and not self.dangling_edges


@dataclass(frozen=True, slots=True)
class PathStep:
    node_id: str
    title: str
    node_class: NodeClass
    via_edge_type: EdgeType | None = None
    edge_confidence: float | None = None
    extract: str | None = None
    source: str | None = None
    hash: str | None = None


@dataclass(frozen=True, slots=True)
class ConflictRef:
    edge_id: str
    source_id: str
    target_id: str
    source_title: str
    target_title: str
    confidence: float | None
    extract: str | None


@dataclass(frozen=True, slots=True)
class PathExplanation:
    node_id: str
    found: bool
    reason: str | None
    steps: tuple[PathStep, ...]
    contradictions: tuple[ConflictRef, ...]
    confidence: float | None          # product of known edge confidences along the trail
    unknown_confidence_edges: int
    text: str


def _s(value: Any) -> str | None:
    return None if value is None else str(value)


def _incident_extract(metrics: Any) -> str | None:
    """Plain restatement of persisted metric deltas (no interpretation)."""
    parts = []
    for m in metrics or []:
        if not isinstance(m, Mapping):
            continue
        label = m.get("label")
        before, after, delta, unit = m.get("before"), m.get("after"), m.get("delta"), m.get("unit") or ""
        if label is None:
            continue
        if before is not None and after is not None:
            parts.append(f"{label}: {before}{unit} -> {after}{unit}" + (f" ({delta:+}{unit})" if isinstance(delta, int | float) else ""))
        elif delta is not None:
            parts.append(f"{label}: {delta}{unit}")
    return "; ".join(parts) or None


class EvidenceGraph:
    """Wrapper around a NetworkX MultiDiGraph (parallel edges of different types are all preserved)."""

    def __init__(self, incident_id: Any, root_id: str | None = None):
        self.incident_id = incident_id
        self.root_id = root_id
        self.g: nx.MultiDiGraph = nx.MultiDiGraph()
        self.report = ValidationReport()
        self._levels: dict[str, int] = {}

    # ---- construction -------------------------------------------------------------------------------
    def add_node(self, node: NodeData) -> bool:
        if node.id in self.g:
            self.report.warnings.append(f"duplicate node id {node.id}; first occurrence kept")
            return False
        self.g.add_node(node.id, data=node)
        return True

    def add_edge(self, edge: EdgeData) -> None:
        self.g.add_edge(edge.source, edge.target, key=edge.id, data=edge)

    # ---- accessors ----------------------------------------------------------------------------------
    def node(self, node_id: str) -> NodeData:
        return self.g.nodes[str(node_id)]["data"]

    def nodes(self) -> list[NodeData]:
        return [d["data"] for _, d in self.g.nodes(data=True)]

    def edges(self) -> list[EdgeData]:
        return [d["data"] for _, _, _, d in self.g.edges(keys=True, data=True)]

    def conflicts(self) -> list[EdgeData]:
        return [e for e in self.edges() if e.type == EdgeType.CONTRADICTS]

    def __contains__(self, node_id: object) -> bool:
        return str(node_id) in self.g

    # ---- validation ---------------------------------------------------------------------------------
    def _projection(self, *, exclude_back: bool = False, exclude_types: Sequence[EdgeType] = ()) -> nx.DiGraph:
        p = nx.DiGraph()
        p.add_nodes_from(self.g.nodes)
        for e in self.edges():
            if (exclude_back and e.back_edge) or e.type in exclude_types:
                continue
            p.add_edge(e.source, e.target)
        return p

    def validate(self) -> ValidationReport:
        """Detect cycles, mark cycle-closing edges as back edges, compute levels, check provenance."""
        rep = self.report
        proj = self._projection()
        rep.cycles = []
        for e in self.edges():
            e.back_edge = False

        if not nx.is_directed_acyclic_graph(proj):
            for cyc in itertools.islice(nx.simple_cycles(proj), MAX_REPORTED_CYCLES):
                rep.cycles.append([*cyc, cyc[0]])
            for u, v in self._back_edges(proj):
                for key in self.g[u][v]:
                    self.g[u][v][key]["data"].back_edge = True
            rep.warnings.append(
                f"{len(rep.cycles)} cycle(s) detected; cycle-closing edges are kept but flagged back_edge"
            )

        rep.nodes_missing_provenance = [
            n.id for n in self.nodes() if self._node_provenance_gap(n)
        ]
        rep.edges_missing_provenance = [
            e.id for e in self.edges() if not e.derived and e.provenance.missing
        ]
        self._compute_levels()
        rep.disconnected_nodes = self._disconnected()
        rep.warnings = [w for w in rep.warnings if not w.startswith("contradicting evidence")]
        rep.warnings.extend(self._unlinked_contradiction_warnings())
        return rep

    @staticmethod
    def _back_edges(proj: nx.DiGraph) -> list[tuple[str, str]]:
        """Iterative DFS back edges (deterministic by sorted neighbours)."""
        color: dict[str, int] = {}
        back: list[tuple[str, str]] = []
        for start in sorted(proj.nodes):
            if color.get(start):
                continue
            color[start] = 1
            stack = [(start, iter(sorted(proj.successors(start))))]
            while stack:
                node, it = stack[-1]
                advanced = False
                for nxt in it:
                    state = color.get(nxt, 0)
                    if state == 1:
                        back.append((node, nxt))
                    elif state == 0:
                        color[nxt] = 1
                        stack.append((nxt, iter(sorted(proj.successors(nxt)))))
                        advanced = True
                        break
                if not advanced:
                    color[node] = 2
                    stack.pop()
        return back

    @staticmethod
    def _node_provenance_gap(n: NodeData) -> bool:
        essential = {"source", "timestamp", "retrieval_method"}
        if n.role == NodeRole.EVIDENCE and n.status not in _NO_HASH_STATUSES:
            essential.add("hash")
        if n.role == NodeRole.HYPOTHESIS:
            essential = {"source", "timestamp"}
        return bool(essential & set(n.provenance.missing))

    def _unlinked_contradiction_warnings(self) -> list[str]:
        out = []
        for n in self.nodes():
            if n.role != NodeRole.EVIDENCE:
                continue
            support = n.data.get("support_score") or 0.0
            contra = n.data.get("contradiction_score") or 0.0
            if contra >= CONTRADICTION_FLAG_THRESHOLD and contra > support:
                touches = any(
                    e.type == EdgeType.CONTRADICTS
                    for e in (*self._in_edges(n.id), *self._out_edges(n.id))
                )
                if not touches:
                    out.append(f"contradicting evidence {n.id} has no contradicts edge")
        return out

    def _in_edges(self, node_id: str) -> list[EdgeData]:
        return [d["data"] for _, _, d in self.g.in_edges(node_id, data=True)]

    def _out_edges(self, node_id: str) -> list[EdgeData]:
        return [d["data"] for _, _, d in self.g.out_edges(node_id, data=True)]

    # ---- layout -------------------------------------------------------------------------------------
    def _compute_levels(self) -> None:
        dag = self._projection(exclude_back=True)
        order = list(nx.lexicographical_topological_sort(dag))

        def layer(nodes: Iterable[str], base: int, seeds: Mapping[str, int] | None = None) -> dict[str, int]:
            keep = set(nodes)
            lv = dict(seeds or {})
            for n in order:
                if n not in keep:
                    continue
                preds = [lv[p] + 1 for p in dag.predecessors(n) if p in lv and p in keep]
                lv[n] = max(preds, default=base)
            return lv

        if self.root_id and self.root_id in dag:
            reach = nx.descendants(dag, self.root_id) | {self.root_id}
            levels = layer(reach, 0)
            rest = [n for n in order if n not in reach]
            if rest:
                base = max(levels.values(), default=0) + 1
                levels.update(layer(rest, base))
        else:
            levels = layer(order, 0)
        self._levels = levels

    def _disconnected(self) -> list[str]:
        if not self.root_id or self.root_id not in self.g:
            return []
        und = self.g.to_undirected(as_view=True)
        connected = nx.node_connected_component(und, self.root_id)
        return sorted(set(self.g.nodes) - connected)

    def level_of(self, node_id: str) -> int:
        return self._levels.get(str(node_id), 0)

    def levels(self) -> list[list[str]]:
        """Nodes grouped by level, deterministic order inside each level."""
        buckets: dict[int, list[str]] = {}
        for nid, lv in self._levels.items():
            buckets.setdefault(lv, []).append(nid)
        return [self._sorted_ids(buckets[k]) for k in sorted(buckets)]

    def topological_order(self) -> list[str]:
        dag = self._projection(exclude_back=True)
        return list(nx.lexicographical_topological_sort(dag, key=self._sort_key))

    def _sort_key(self, nid: str) -> tuple:
        n = self.node(nid)
        return (self._levels.get(nid, 0), _ROLE_ORDER[n.role], n.title, nid)

    def _sorted_ids(self, ids: Iterable[str]) -> list[str]:
        return sorted(ids, key=self._sort_key)

    # ---- explanation --------------------------------------------------------------------------------
    def explain_path(self, node_id: Any) -> PathExplanation:
        """Trail from the root symptom to `node_id` with per-hop provenance, plus preserved contradictions."""
        nid = str(node_id)
        if nid not in self.g:
            raise KeyError(f"unknown node {nid}")
        target = self.node(nid)
        trail = self._projection(exclude_back=True, exclude_types=(EdgeType.CONTRADICTS,))
        path: list[str] | None = None
        reason: str | None = None
        if not self.root_id or self.root_id not in self.g:
            reason = "graph has no root node"
        elif nid == self.root_id:
            path = [nid]
        else:
            try:
                path = nx.shortest_path(trail, self.root_id, nid)
            except nx.NetworkXNoPath:
                reason = "node is not reachable from the root through non-contradicting edges"

        steps: list[PathStep] = []
        confs: list[float] = []
        unknown = 0
        if path:
            for i, pid in enumerate(path):
                n = self.node(pid)
                edge = self._best_edge(path[i - 1], pid) if i else None
                if edge is not None:
                    if edge.confidence is None:
                        unknown += 1
                    else:
                        confs.append(edge.confidence)
                steps.append(PathStep(
                    node_id=pid, title=n.title, node_class=n.node_class,
                    via_edge_type=edge.type if edge else None,
                    edge_confidence=edge.confidence if edge else None,
                    extract=(edge.provenance.extract if edge else None) or n.extract,
                    source=(edge.provenance.source if edge else None) or n.source,
                    hash=(edge.provenance.hash if edge else None) or n.provenance.hash,
                ))
        else:
            steps.append(PathStep(node_id=nid, title=target.title, node_class=target.node_class,
                                  extract=target.extract, source=target.source, hash=target.provenance.hash))

        scope = path or [nid]
        conflicts = self._conflicts_touching(scope)
        confidence = None
        if path and len(path) > 1 and not unknown:
            confidence = 1.0
            for c in confs:
                confidence *= c
        return PathExplanation(
            node_id=nid, found=bool(path), reason=reason, steps=tuple(steps), contradictions=tuple(conflicts),
            confidence=confidence, unknown_confidence_edges=unknown, text=self._render(steps, conflicts, reason),
        )

    def _best_edge(self, u: str, v: str) -> EdgeData | None:
        cands = [d["data"] for d in self.g[u][v].values() if d["data"].type != EdgeType.CONTRADICTS
                 and not d["data"].back_edge]
        return max(cands, key=lambda e: (-1.0 if e.confidence is None else e.confidence, e.id), default=None)

    def _conflicts_touching(self, nodes: Sequence[str]) -> list[ConflictRef]:
        seen: set[str] = set()
        out = []
        for e in self.conflicts():
            if e.id in seen or not ({e.source, e.target} & set(nodes)):
                continue
            seen.add(e.id)
            out.append(ConflictRef(
                edge_id=e.id, source_id=e.source, target_id=e.target,
                source_title=self.node(e.source).title, target_title=self.node(e.target).title,
                confidence=e.confidence, extract=e.provenance.extract,
            ))
        return sorted(out, key=lambda c: c.edge_id)

    @staticmethod
    def _render(steps: Sequence[PathStep], conflicts: Sequence[ConflictRef], reason: str | None) -> str:
        lines = []
        for s in steps:
            hop = f"--{s.via_edge_type.value}" + (f" ({s.edge_confidence:.2f})" if s.edge_confidence is not None else "") + "--> " \
                if s.via_edge_type else ""
            lines.append(f"{hop}[{s.node_class.value}] {s.title}")
        text = " ".join(lines)
        if reason:
            text += f"  (no trail: {reason})"
        for c in conflicts:
            text += f"\n  CONFLICT: '{c.source_title}' contradicts '{c.target_title}'"
        return text

    # ---- serialization ------------------------------------------------------------------------------
    def serialize(self) -> GraphOut:
        if not self._levels and self.g.number_of_nodes():
            self.validate()
        nodes = []
        for nid in self._sorted_ids(self.g.nodes):
            n = self.node(nid)
            nodes.append(GraphNode(
                id=n.id, node_class=n.node_class, role=n.role, title=n.title, level=self.level_of(nid),
                status=n.status, evidence_id=n.evidence_id, hypothesis_id=n.hypothesis_id, source=n.source,
                url=n.url, observed_at=n.observed_at, confidence=n.confidence, extract=n.extract,
                provenance=ProvenanceOut.model_validate(n.provenance.to_dict()), data=_jsonable(n.data),
            ))
        edges = [
            GraphEdge(
                id=e.id, source=e.source, target=e.target, type=e.type, confidence=e.confidence,
                provenance=ProvenanceOut.model_validate(e.provenance.to_dict()),
                conflict=e.type == EdgeType.CONTRADICTS, derived=e.derived, back_edge=e.back_edge,
            )
            for e in sorted(self.edges(), key=lambda e: (self.level_of(e.source), self.level_of(e.target), e.id))
        ]
        r = self.report
        return GraphOut(
            incident_id=self.incident_id,
            root_id=self.root_id,
            nodes=nodes,
            edges=edges,
            levels=self.levels(),
            topological_order=self.topological_order(),
            validation=GraphValidationOut(
                is_acyclic=r.is_acyclic, cycles=r.cycles, dangling_edges=r.dangling_edges,
                nodes_missing_provenance=r.nodes_missing_provenance,
                edges_missing_provenance=r.edges_missing_provenance,
                disconnected_nodes=r.disconnected_nodes, warnings=r.warnings,
            ),
            counts={
                "nodes": len(nodes), "edges": len(edges),
                "conflicts": sum(1 for e in edges if e.conflict),
                "derived_edges": sum(1 for e in edges if e.derived),
                "dangling_edges": len(r.dangling_edges),
            },
        )


# ---- builders ---------------------------------------------------------------------------------------
def _evidence_node(ev: Any) -> NodeData:
    status = _s(_get(ev, "status"))
    etype = _s(_get(ev, "type")) or EvidenceType.EXTERNAL.value
    prov = Provenance.from_evidence(ev)
    ev_id = _get(ev, "id")
    return NodeData(
        id=str(ev_id), node_class=_EVIDENCE_CLASS.get(etype, NodeClass.EXTERNAL), role=NodeRole.EVIDENCE,
        title=_get(ev, "title") or _get(ev, "url") or str(ev_id), provenance=prov, status=status,
        evidence_id=ev_id, source=_get(ev, "source") or domain_of(_get(ev, "url")), url=_get(ev, "url"),
        observed_at=_get(ev, "observed_at") or _get(ev, "retrieved_at"), confidence=_get(ev, "confidence"),
        extract=_get(ev, "excerpt"),
        data={k: _get(ev, k) for k in ("support_score", "contradiction_score", "insufficient_score",
                                       "freshness_risk", "retrieved_at", "retrieval_method", "type")},
    )


def _synthetic_node(row: Any) -> NodeData:
    kind = _s(_get(row, "kind")) or "root"
    node_class, role = _SYNTHETIC_CLASS.get(kind, (NodeClass.INFERENCE, NodeRole.EVIDENCE))
    prov = Provenance.from_evidence({
        "source": _get(row, "source"), "retrieved_at": _get(row, "observed_at"),
        "confidence": _get(row, "confidence"), "excerpt": _get(row, "excerpt"),
        "content_hash": _get(row, "content_hash"), "retrieval_method": _get(row, "retrieval_method"),
    })
    return NodeData(
        id=str(_get(row, "id")), node_class=node_class, role=role, title=_get(row, "title") or kind,
        provenance=prov, source=_get(row, "source"), observed_at=_get(row, "observed_at"),
        confidence=_get(row, "confidence"), extract=_get(row, "excerpt"),
        data={"kind": kind, "ref_type": _get(row, "ref_type"), "ref_id": _get(row, "ref_id"),
              **(_get(row, "data") or {})},
    )


def _incident_root(incident: Any) -> NodeData:
    metrics = _get(incident, "metrics") or []
    extract = _incident_extract(metrics)
    ts = _get(incident, "first_observed_at") or _get(incident, "detected_at")
    prov = Provenance.make(
        source="profound", timestamp=ts, confidence=_get(incident, "confidence"), extract=extract,
        hash=hash_json(metrics) if metrics else None, retrieval_method="incident_detector",
    )
    return NodeData(
        id=str(_get(incident, "id")), node_class=NodeClass.PROFOUND, role=NodeRole.ROOT,
        title=_get(incident, "title") or "Incident", provenance=prov, status=None, source="profound",
        observed_at=ts, confidence=_get(incident, "confidence"), extract=extract,
        data={"incident_number": _get(incident, "number"), "category": _s(_get(incident, "category")),
              "severity": _s(_get(incident, "severity")), "metrics": metrics},
    )


def _hypothesis_node(h: Any) -> NodeData:
    produced_by = _get(h, "produced_by") or "rules"
    evidence_ids = [str(i) for i in (_get(h, "evidence_ids") or [])]
    prov = Provenance.make(
        source=produced_by, timestamp=_get(h, "created_at"), confidence=_get(h, "confidence"), extract=None,
        hash=hash_json({"title": _get(h, "title"), "summary": _get(h, "summary"),
                        "evidence_ids": sorted(evidence_ids), "rationale": _get(h, "rationale")}),
        retrieval_method="inference",
    )
    return NodeData(
        id=str(_get(h, "id")), node_class=NodeClass.INFERENCE, role=NodeRole.HYPOTHESIS, title=_get(h, "title") or "",
        provenance=prov, status=_s(_get(h, "status")), hypothesis_id=_get(h, "id"), source=produced_by,
        observed_at=_get(h, "created_at"), confidence=_get(h, "confidence"),
        data={"summary": _get(h, "summary"), "rationale": _get(h, "rationale"), "evidence_ids": evidence_ids,
              "interpretation": True},
    )


def _persisted_edge(row: Any, src: str, dst: str) -> EdgeData:
    prov_json = dict(_get(row, "provenance") or {})
    col_conf = _get(row, "confidence")
    if prov_json.get("confidence") is None and col_conf is not None:
        prov_json["confidence"] = col_conf
    prov = Provenance.from_mapping(prov_json)
    et = _get(row, "edge_type")
    return EdgeData(
        id=str(_get(row, "id")), source=src, target=dst, type=EdgeType(et),
        provenance=prov, confidence=col_conf if col_conf is not None else prov.confidence,
    )


def build_graph(
    incident: Any,
    evidence: Iterable[Any],
    edges: Iterable[Any],
    hypotheses: Iterable[Any],
    nodes: Iterable[Any] = (),
    *,
    derive_hypothesis_edges: bool = True,
    strict: bool = False,
) -> EvidenceGraph:
    """Build the incident's evidence graph from persisted rows.

    strict=True raises GraphIntegrityError on dangling edges or cycles; otherwise they are reported in
    `graph.report` (and in GraphOut.validation) while every resolvable node/edge is retained.
    """
    nodes = list(nodes)
    root_rows = [n for n in nodes if _s(_get(n, "kind")) == "root"]
    incident_id = _get(incident, "id")
    graph = EvidenceGraph(incident_id)
    alias: dict[str, str] = {}

    if root_rows:
        root = _synthetic_node(root_rows[0])
        alias[str(incident_id)] = root.id
        graph.add_node(root)
        for extra in root_rows[1:]:
            graph.report.warnings.append(f"extra root node {_get(extra, 'id')} ignored as root; kept as node")
    else:
        root = _incident_root(incident)
        graph.add_node(root)
    graph.root_id = root.id

    for row in nodes:
        if root_rows and row is root_rows[0]:
            continue
        graph.add_node(_synthetic_node(row))
    evidence = list(evidence)
    for ev in evidence:
        graph.add_node(_evidence_node(ev))
    hypotheses = list(hypotheses)
    for h in hypotheses:
        graph.add_node(_hypothesis_node(h))

    for row in edges:
        src, dst = str(_get(row, "src_id")), str(_get(row, "dst_id"))
        src, dst = alias.get(src, src), alias.get(dst, dst)
        missing = [x for x in (src, dst) if x not in graph]
        if missing:
            graph.report.dangling_edges.append({
                "edge_id": str(_get(row, "id")), "src_id": src, "dst_id": dst,
                "edge_type": _s(_get(row, "edge_type")), "missing": missing,
            })
            continue
        graph.add_edge(_persisted_edge(row, src, dst))

    if derive_hypothesis_edges:
        _add_hypothesis_edges(graph, hypotheses)

    report = graph.validate()
    if strict and not report.ok:
        raise GraphIntegrityError(
            f"evidence graph integrity failure: {len(report.cycles)} cycle(s), "
            f"{len(report.dangling_edges)} dangling edge(s)", report,
        )
    return graph


def _add_hypothesis_edges(graph: EvidenceGraph, hypotheses: Sequence[Any]) -> None:
    """Hypothesis.evidence_ids that have no persisted edge to the hypothesis become `derived` supports edges."""
    for h in hypotheses:
        hid = str(_get(h, "id"))
        for raw_id in _get(h, "evidence_ids") or []:
            eid = str(raw_id)
            if eid not in graph:
                graph.report.dangling_edges.append({
                    "edge_id": None, "src_id": eid, "dst_id": hid, "edge_type": EdgeType.SUPPORTS.value,
                    "missing": [eid], "reason": "hypothesis.evidence_ids references unknown node",
                })
                continue
            if graph.g.has_edge(eid, hid) or graph.g.has_edge(hid, eid):
                continue
            ev = graph.node(eid)
            graph.add_edge(EdgeData(
                id=f"derived:{eid}->{hid}", source=eid, target=hid, type=EdgeType.SUPPORTS,
                provenance=Provenance.make(
                    source=ev.provenance.source, timestamp=ev.provenance.timestamp,
                    extract=ev.provenance.extract, hash=ev.provenance.hash,
                    retrieval_method="hypothesis.evidence_ids",
                ),
                confidence=None, derived=True,
            ))


def hypothesis_out(h: Any, graph: EvidenceGraph | None = None) -> HypothesisOut:
    """Hypothesis row -> schema; contradicting evidence ids come from persisted `contradicts` edges."""
    out = HypothesisOut.model_validate(h)
    hid = str(_get(h, "id"))
    if graph is not None and hid in graph:
        out.contradicting_evidence_ids = [
            graph.node(e.source).evidence_id
            for e in graph._in_edges(hid)
            if e.type == EdgeType.CONTRADICTS and graph.node(e.source).evidence_id is not None
        ]
    return out


def evidence_detail(ev: Any, graph: EvidenceGraph | None = None) -> EvidenceDetail:
    detail = EvidenceDetail.model_validate(ev)
    detail.provenance = ProvenanceOut.model_validate(Provenance.from_evidence(ev).to_dict())
    if graph is not None and str(_get(ev, "id")) in graph:
        eid = str(_get(ev, "id"))
        out = graph.serialize()
        detail.edges_in = [e for e in out.edges if e.target == eid]
        detail.edges_out = [e for e in out.edges if e.source == eid]
    return detail
