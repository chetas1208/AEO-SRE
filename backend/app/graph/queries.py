"""GraphRepository: the seven graph questions + graph_context_v1 features. Read-only, deterministic, no LLM.

Rules enforced here:
- every statement goes through `client.scoped_read` (needs `$organization_id`), parameterized only; the only
  non-parameter fragments are allow-listed labels and clamped integer hop bounds;
- traversal bounded to 1..4 hops, row caps on every query, per-query timeout from `QueryConfig`;
- all history is filtered `occurred_at <= $as_of`; callers pass an explicit `as_of` (default now);
- result post-processing is pure (`_pairs`, `_contention`, ... and `features.py`), so it is unit-testable;
- there is NO arbitrary-Cypher entry point.

Model names (labels / relationships / property names) follow docs/GRAPH_SPEC.md and app/graph/model.py; property
names the model leaves open are collected in `docs/notes/handoff-n3.md` ("model assumptions").
"""
from __future__ import annotations

import asyncio
from collections import Counter
from collections.abc import Awaitable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import structlog

from app.graph.client import GraphClient, get_graph_client, require_org
from app.graph.errors import GraphError, GraphUnavailable, OrganizationScopeError
from app.graph.explain import build_decision_explanation, to_dt
from app.graph.feature_cache import FeatureCache, get_feature_cache, persist_features
from app.graph.features import (
    FEATURE_NAMES,
    FEATURE_VERSION,
    MAX_DEPENDENCY_DEPTH,
    MAX_PATH_HOPS,
    VECTOR_NAMES,
    ChangeFact,
    ConflictFact,
    ContextFacts,
    ExperimentFact,
    TargetFacts,
    as_utc,
    compute_features,
    context_hash,
    encode_vector,
    experiment_active,
)
from app.graph.results import (
    AgentConflictPair,
    AgentConflicts,
    ChangeContext,
    ContradictedClaim,
    ContradictedClaims,
    DecisionExplanation,
    GraphEdge,
    GraphFeatures,
    GraphNode,
    GraphView,
    SimilarContexts,
    TargetContention,
    TargetContentionRow,
)
from app.graph.schema import NODE_IDS
from app.graph.similarity import (
    Candidate,
    ContextSignature,
    decision_rates,
    rank_similar,
    stats_by_decision,
)
from app.graph.staleness import StalenessCheck, default_staleness_check

log = structlog.get_logger()

LINEAGE_REL_TYPES = [
    "STARTED", "EMITTED", "PROPOSED", "MODIFIES", "ALTERS", "ABOUT", "AFFECTS", "CONFLICTS_WITH", "INVOLVES",
    "PROTECTS", "DECIDES_ON", "BASED_ON", "APPROVES", "EXECUTED_AS", "MEASURES", "OBSERVED", "PRODUCED", "SELECTED",
    "USED", "TRIGGERED_BY", "DERIVED_FROM", "FOLLOWED_BY", "ASSOCIATED_WITH", "NEXT",
]
# interior nodes a lineage path may NOT pass through (hubs would pull in unrelated lineage); endpoints are fine
CHANGE_HUBS = ["Target", "Agent", "PromptCluster", "Organization", "PolicyVersion", "Claim"]
EXPERIMENT_HUBS = ["Agent", "PromptCluster", "Organization", "PolicyVersion", "Claim"]
_ID_EXPR = ("coalesce(n.changeset_id, n.experiment_id, n.conflict_id, n.claim_id, n.event_id, n.decision_id, "
            "n.agent_id, n.approval_id, n.outcome_id, n.observation_id, n.prompt_cluster_id, n.agent_run_id)")


@dataclass(frozen=True)
class QueryConfig:
    timeout_s: float | None = None  # None -> NEO4J_QUERY_TIMEOUT_S
    max_rows: int = 500
    neighbor_cap: int = 500
    lineage_paths: int = 400
    similar_candidates: int = 500
    max_hops: int = 4  # hard ceiling 4
    stale_lag_s: float = 120.0
    persist_features: bool = False

    @property
    def hops(self) -> int:
        return max(1, min(int(self.max_hops), 4))


def _hops(n: int | None, cap: int) -> int:
    return max(1, min(int(n if n is not None else cap), cap, 4))


def _strict(x: str) -> str:
    return f"({x}.occurred_at IS NOT NULL AND datetime({x}.occurred_at) <= datetime($as_of))"


def _soft(x: str) -> str:
    return f"({x}.occurred_at IS NULL OR datetime({x}.occurred_at) <= datetime($as_of))"


def _since(x: str) -> str:
    return f"($since IS NULL OR datetime({x}.occurred_at) >= datetime($since))"


def _iso(dt: datetime | None) -> str | None:
    return as_utc(dt).strftime("%Y-%m-%dT%H:%M:%S.%fZ") if dt else None


def _clean_props(p: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in p.items():
        if k in ("organization_id", "app", "projected") or v is None:
            continue
        if hasattr(v, "to_native"):
            v = v.to_native()
        if isinstance(v, datetime):
            out[k] = as_utc(v).isoformat()
        elif isinstance(v, bool | int | float):
            out[k] = v
        elif isinstance(v, str):
            out[k] = v[:500]
    return out


# ------------------------------------------------------------------------------------------ pure post-processing
def build_view(rows: list[dict[str, Any]], *, focal_label: str, focal_id: str, generated_at: datetime,
               as_of: datetime | None, hops: int, cap: int) -> GraphView:
    """Rows from the expansion query -> normalized {nodes, edges, focus_id, generated_at}. Pure."""
    nodes: dict[str, GraphNode] = {}
    eid_to_bid: dict[str, str] = {}
    edges: dict[str, GraphEdge] = {}

    def add_node(n: dict[str, Any]) -> None:
        eid = n["eid"]
        if eid in eid_to_bid:
            return
        label = next((lb for lb in n.get("labels", []) if lb in NODE_IDS), None)
        if label is None:
            return
        bid = n["props"].get(NODE_IDS[label])
        if bid is None:
            return
        bid = str(bid)
        eid_to_bid[eid] = bid
        nodes.setdefault(bid, GraphNode(id=bid, label=label, props=_clean_props(n["props"])))

    found = False
    for row in rows:
        add_node(row["focal"])
        found = True
        for n in row.get("nodes") or []:
            add_node(n)
        for r in row.get("rels") or []:
            s, e = eid_to_bid.get(r["s"]), eid_to_bid.get(r["e"])
            if s and e:
                eid_ = f"{s}|{r['type']}|{e}"
                edges.setdefault(eid_, GraphEdge(id=eid_, source=s, target=e, type=r["type"]))
    return GraphView(
        nodes=sorted(nodes.values(), key=lambda n: (n.label, n.id)), edges=sorted(edges.values(), key=lambda e: e.id),
        focus_id=focal_id if found else None, generated_at=generated_at, as_of=as_of, max_hops=hops,
        truncated=sum(1 for r in rows if r.get("rels")) >= cap, found=found)


def build_agent_pairs(rows: list[dict[str, Any]]) -> list[AgentConflictPair]:
    seen: set[tuple[str, str, str]] = set()
    acc: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        a, b, kid = str(r["a"]), str(r["b"]), str(r["conflict_id"])
        if a > b:
            a, b = b, a
        if (a, b, kid) in seen:
            continue
        seen.add((a, b, kid))
        p = acc.setdefault((a, b), {"types": Counter(), "targets": set(), "last": None, "ids": []})
        p["types"][str(r.get("conflict_type") or "UNKNOWN")] += 1
        p["targets"].update(t for t in (r.get("shared") or []) + (r.get("aff") or []) if t)
        at = to_dt(r.get("at"))
        if at and (p["last"] is None or at > p["last"]):
            p["last"] = at
        p["ids"].append(kid)
    out = [AgentConflictPair(agent_a=a, agent_b=b, conflict_count=len(p["ids"]), conflict_types=dict(sorted(p["types"].items())),
                             targets=sorted(p["targets"]), last_at=p["last"], conflict_ids=sorted(p["ids"]))
           for (a, b), p in acc.items()]
    out.sort(key=lambda x: (-x.conflict_count, x.agent_a, x.agent_b))
    return out


def build_contention(rows: list[dict[str, Any]], as_of: datetime, limit: int) -> list[TargetContentionRow]:
    out: list[TargetContentionRow] = []
    for r in rows:
        t = r["target"]
        changes = r.get("changes") or []
        conflicts = {k for k in (r.get("affecting") or [])}
        for c in changes:
            conflicts.update(c.get("conflicts") or [])
        exps = [_experiment(e) for e in r.get("exps") or []]
        n_active = sum(1 for e in exps if e and experiment_active(e, as_of))
        row = TargetContentionRow(
            target_id=str(t.get("target_id")), target_key=t.get("target_key"), protected=bool(t.get("protected")),
            change_count=len({c["id"] for c in changes}), agent_count=len({c.get("agent") for c in changes if c.get("agent")}),
            conflict_count=len(conflicts), active_experiment_count=n_active)
        row.score = float(row.change_count + 2 * row.conflict_count + 3 * row.active_experiment_count)
        if row.change_count or row.conflict_count:
            out.append(row)
    out.sort(key=lambda x: (-x.score, x.target_id))
    return out[:limit]


def build_claims(rows: list[dict[str, Any]], min_count: int, limit: int) -> list[ContradictedClaim]:
    acc: dict[str, dict[str, Any]] = {}
    for r in rows:
        cl = r["claim"]
        cid = str(cl.get("claim_id"))
        a = acc.setdefault(cid, {"claim": cl, "conflicts": set(), "changes": set(), "agents": set(), "last": None})
        a["conflicts"].add(str(r["conflict_id"]))
        for c in r.get("changes") or []:
            a["changes"].add(str(c["id"]))
            if c.get("agent"):
                a["agents"].add(str(c["agent"]))
        at = to_dt(r.get("at"))
        if at and (a["last"] is None or at > a["last"]):
            a["last"] = at
    out = []
    for cid, a in acc.items():
        if len(a["conflicts"]) < min_count:
            continue
        cl = a["claim"]
        out.append(ContradictedClaim(
            claim_id=cid, claim_text=cl.get("text") or cl.get("claim_text") or cl.get("normalized_text"),
            claim_type=cl.get("claim_type"), conflict_count=len(a["conflicts"]), changeset_count=len(a["changes"]),
            agent_count=len(a["agents"]), last_at=a["last"], conflict_ids=sorted(a["conflicts"])))
    out.sort(key=lambda x: (-x.conflict_count, x.claim_id))
    return out[:limit]


def _experiment(p: dict[str, Any] | None) -> ExperimentFact | None:
    if not p or p.get("experiment_id") is None:
        return None
    return ExperimentFact(
        id=str(p["experiment_id"]),
        started_at=to_dt(p.get("started_at") or p.get("starts_at") or p.get("start_at")),
        ends_at=to_dt(p.get("ends_at") or p.get("end_at") or p.get("lock_until")),
        status=p.get("status"))


def _conflict(p: dict[str, Any]) -> ConflictFact:
    return ConflictFact(id=str(p["id"]), type=p.get("type"), at=to_dt(p.get("at")),
                        others=[str(o) for o in p.get("others") or []])


def _change(p: dict[str, Any]) -> ChangeFact:
    return ChangeFact(id=str(p["id"]), at=to_dt(p.get("at")), agent=p.get("agent"),
                      conflicts=[_conflict(k) for k in p.get("conflicts") or []],
                      target_ids=[str(t) for t in p.get("targets") or []])


def _first(vals: Sequence[Any]) -> Any:
    return next((v for v in sorted(str(x) for x in vals if x is not None)), None)


# ------------------------------------------------------------------------------------------------ repository
class GraphRepository:
    def __init__(self, client: GraphClient | None = None, config: QueryConfig | None = None,
                 cache: FeatureCache | None = None):
        self._client = client
        self.config = config or QueryConfig()
        self.cache = cache if cache is not None else get_feature_cache()

    @property
    def client(self) -> GraphClient:
        return self._client or get_graph_client()

    async def _read(self, org: str, query: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return await self.client.scoped_read(org, query, params, timeout=self.config.timeout_s)

    @staticmethod
    def _as_of(as_of: datetime | None) -> datetime:
        return as_utc(as_of) if as_of else datetime.now(UTC)

    # ------------------------------------------------------------- Q2 / lineage views
    async def _expand(self, org: str, label: str, id_prop: str, focal_id: str,
                      hubs: list[str], hops: int, as_of: datetime) -> GraphView:
        """Bounded undirected expansion over provenance relationship types. `label`/`id_prop` come from the NODE_IDS
        allow-list and `hops` is clamped to 1..4: the only values interpolated into the statement."""
        h = _hops(hops, self.config.hops)
        q = (
            f"MATCH (f:{label} {{{id_prop}: $focal_id, organization_id: $organization_id}}) "
            f"OPTIONAL MATCH p=(f)-[*1..{h}]-(n) "
            "WHERE all(r IN relationships(p) WHERE type(r) IN $rel_types) "
            "AND all(x IN nodes(p) WHERE x.organization_id = $organization_id AND " + _soft("x") + ") "
            "AND none(x IN nodes(p)[1..-1] WHERE any(l IN labels(x) WHERE l IN $hubs)) "
            "WITH f, p LIMIT $cap "
            "RETURN {eid: elementId(f), labels: labels(f), props: properties(f)} AS focal, "
            "CASE WHEN p IS NULL THEN [] ELSE [x IN nodes(p) | {eid: elementId(x), labels: labels(x), props: properties(x)}] END AS nodes, "
            "CASE WHEN p IS NULL THEN [] ELSE [r IN relationships(p) | {type: type(r), s: elementId(startNode(r)), e: elementId(endNode(r))}] END AS rels"
        )
        rows = await self._read(org, q, {
            "focal_id": focal_id, "rel_types": LINEAGE_REL_TYPES, "hubs": hubs, "cap": self.config.lineage_paths,
            "as_of": _iso(as_of)})
        return build_view(rows, focal_label=label, focal_id=focal_id, generated_at=datetime.now(UTC), as_of=as_of,
                          hops=h, cap=self.config.lineage_paths)

    async def get_change_lineage(self, organization_id: Any, changeset_id: str, *, as_of: datetime | None = None,
                                 max_hops: int | None = None) -> GraphView:
        """Question 2: AgentRun -> Event -> ChangeSet -> Conflict -> Decision -> Approval -> Execution ->
        Observation -> Outcome. Upstream/downstream of the ChangeSet (<= max_hops) plus the downstream chain of each
        of its Decisions (<= max_hops), merged and de-duplicated."""
        org = require_org(organization_id)
        A = self._as_of(as_of)
        base = await self._expand(org, "ChangeSet", "changeset_id", str(changeset_id), CHANGE_HUBS,
                                  max_hops or 4, A)
        if not base.found:
            return base
        decisions = [n.id for n in base.nodes if n.label == "Decision"]
        extra: list[GraphView] = []
        for did in decisions[:5]:
            extra.append(await self._expand(org, "Decision", "decision_id", did, CHANGE_HUBS, max_hops or 4, A))
        return self._merge_views(base, extra, str(changeset_id))

    @staticmethod
    def _merge_views(base: GraphView, extra: list[GraphView], focus: str) -> GraphView:
        nodes = {n.id: n for n in base.nodes}
        edges = {e.id: e for e in base.edges}
        truncated = base.truncated
        for v in extra:
            truncated = truncated or v.truncated
            nodes.update({n.id: n for n in v.nodes if n.id not in nodes})
            edges.update({e.id: e for e in v.edges if e.id not in edges})
        return base.model_copy(update={
            "nodes": sorted(nodes.values(), key=lambda n: (n.label, n.id)),
            "edges": sorted(edges.values(), key=lambda e: e.id), "focus_id": focus, "truncated": truncated})

    async def get_experiment_lineage(self, organization_id: Any, experiment_id: str, *, as_of: datetime | None = None,
                                     max_hops: int | None = None) -> GraphView:
        org = require_org(organization_id)
        return await self._expand(org, "Experiment", "experiment_id", str(experiment_id), EXPERIMENT_HUBS,
                                  max_hops or 3, self._as_of(as_of))

    # ------------------------------------------------------------- Q1 explanation
    async def get_decision_explanation(self, organization_id: Any, changeset_id: str, *,
                                       as_of: datetime | None = None) -> DecisionExplanation:
        org = require_org(organization_id)
        A = self._as_of(as_of)
        q = (
            "MATCH (c:ChangeSet {changeset_id: $changeset_id, organization_id: $organization_id}) "
            "RETURN properties(c) AS change, "
            "[(c)-[:MODIFIES]->(t:Target) WHERE t.organization_id = $organization_id | properties(t)][..$cap] AS targets, "
            "[(d:Decision)-[:DECIDES_ON]->(c) WHERE d.organization_id = $organization_id AND " + _strict("d") + " | "
            "{decision: properties(d), conflicts: ["
            "(d)-[:BASED_ON]->(k:Conflict) WHERE k.organization_id = $organization_id | "
            "{conflict: properties(k), "
            "experiments: [(k)-[:INVOLVES|PROTECTS]->(e:Experiment) WHERE e.organization_id = $organization_id | "
            "{experiment: properties(e), measures: [(e)-[:MEASURES]->(t2:Target) WHERE t2.organization_id = $organization_id | properties(t2)][..$cap]}][..$cap], "
            "claims: [(k)-[:ABOUT]->(cl:Claim) WHERE cl.organization_id = $organization_id | properties(cl)][..$cap]}][..$cap]}][..$cap] AS decisions"
        )
        rows = await self._read(org, q, {"changeset_id": str(changeset_id), "as_of": _iso(A), "cap": 50})
        return build_decision_explanation(str(changeset_id), rows[0] if rows else None, as_of=A,
                                          generated_at=datetime.now(UTC))

    # ------------------------------------------------------------- Q4 agent collisions
    async def get_agent_conflicts(self, organization_id: Any, *, agent_id: str | None = None,
                                  since: datetime | None = None, as_of: datetime | None = None) -> AgentConflicts:
        org = require_org(organization_id)
        A = self._as_of(as_of)
        q = (
            "MATCH (k:Conflict)-[:INVOLVES]->(c1:ChangeSet)<-[:PROPOSED]-(a1:Agent), "
            "(k)-[:INVOLVES]->(c2:ChangeSet)<-[:PROPOSED]-(a2:Agent) "
            "WHERE k.organization_id = $organization_id AND c1.organization_id = $organization_id "
            "AND c2.organization_id = $organization_id AND a1.organization_id = $organization_id "
            "AND a2.organization_id = $organization_id AND a1.agent_id < a2.agent_id AND " + _strict("k") + " AND " + _since("k") +
            " AND ($agent_id IS NULL OR a1.agent_id = $agent_id OR a2.agent_id = $agent_id) "
            "RETURN a1.agent_id AS a, a2.agent_id AS b, k.conflict_id AS conflict_id, k.conflict_type AS conflict_type, "
            "k.occurred_at AS at, [(c1)-[:MODIFIES]->(t:Target)<-[:MODIFIES]-(c2) | t.target_key] AS shared, "
            "[(k)-[:AFFECTS]->(t2:Target) | t2.target_key] AS aff "
            "ORDER BY a, b, conflict_id LIMIT $cap"
        )
        rows = await self._read(org, q, {"agent_id": agent_id, "since": _iso(since), "as_of": _iso(A),
                                         "cap": self.config.max_rows})
        return AgentConflicts(pairs=build_agent_pairs(rows), generated_at=datetime.now(UTC), since=since, as_of=A,
                              truncated=len(rows) >= self.config.max_rows)

    # ------------------------------------------------------------- Q5 contention
    async def get_target_contention(self, organization_id: Any, *, since: datetime | None = None,
                                    as_of: datetime | None = None, limit: int = 20) -> TargetContention:
        org = require_org(organization_id)
        A = self._as_of(as_of)
        limit = max(1, min(int(limit), self.config.max_rows))
        q = (
            "MATCH (t:Target) WHERE t.organization_id = $organization_id "
            "WITH t, [(c:ChangeSet)-[:MODIFIES]->(t) WHERE c.organization_id = $organization_id AND " + _strict("c") +
            " AND " + _since("c") + " | {id: c.changeset_id, agent: head([(a:Agent)-[:PROPOSED]->(c) | a.agent_id]), "
            "conflicts: [(k:Conflict)-[:INVOLVES]->(c) WHERE k.organization_id = $organization_id AND " + _strict("k") +
            " | k.conflict_id]}] AS changes "
            "WITH t, changes, [(k:Conflict)-[:AFFECTS]->(t) WHERE k.organization_id = $organization_id AND " + _strict("k") +
            " AND " + _since("k") + " | k.conflict_id] AS affecting "
            "RETURN properties(t) AS target, changes, affecting, "
            "[(e:Experiment)-[:MEASURES]->(t) WHERE e.organization_id = $organization_id | properties(e)] AS exps "
            "ORDER BY size(changes) + size(affecting) DESC, t.target_id LIMIT $cap"
        )
        rows = await self._read(org, q, {"since": _iso(since), "as_of": _iso(A), "cap": self.config.max_rows})
        return TargetContention(rows=build_contention(rows, A, limit), generated_at=datetime.now(UTC), since=since,
                                as_of=A, truncated=len(rows) >= self.config.max_rows)

    # ------------------------------------------------------------- Q6 contradicted claims
    async def get_contradicted_claims(self, organization_id: Any, *, min_count: int = 2, since: datetime | None = None,
                                      as_of: datetime | None = None, limit: int = 20) -> ContradictedClaims:
        org = require_org(organization_id)
        A = self._as_of(as_of)
        limit = max(1, min(int(limit), self.config.max_rows))
        q = (
            "MATCH (k:Conflict)-[:ABOUT|AFFECTS|INVOLVES]->(cl:Claim) "
            "WHERE k.organization_id = $organization_id AND cl.organization_id = $organization_id AND " + _strict("k") +
            " AND " + _since("k") + " AND (toLower(coalesce(k.conflict_type, '')) CONTAINS 'canonical' "
            "OR toLower(coalesce(k.conflict_type, '')) CONTAINS 'contradict') "
            "RETURN properties(cl) AS claim, k.conflict_id AS conflict_id, k.occurred_at AS at, "
            "[(k)-[:INVOLVES]->(c:ChangeSet) WHERE c.organization_id = $organization_id | "
            "{id: c.changeset_id, agent: head([(a:Agent)-[:PROPOSED]->(c) | a.agent_id])}] AS changes "
            "ORDER BY conflict_id LIMIT $cap"
        )
        rows = await self._read(org, q, {"since": _iso(since), "as_of": _iso(A), "cap": self.config.max_rows})
        return ContradictedClaims(claims=build_claims(rows, min_count, limit), generated_at=datetime.now(UTC),
                                  since=since, as_of=A, min_count=min_count, truncated=len(rows) >= self.config.max_rows)

    # ------------------------------------------------------------- facts for context / features / similarity
    async def _resolve(self, org: str, ctx: ChangeContext, A: datetime) -> tuple[ChangeContext, bool, list[ConflictFact] | None, set[str]]:
        """Fill the context from the projected ChangeSet (explicit values win). Returns (ctx, found, own conflicts,
        experiment ids implicated by own conflicts)."""
        if not ctx.changeset_id:
            return ctx, False, None, set()
        q = (
            "MATCH (c:ChangeSet {changeset_id: $changeset_id, organization_id: $organization_id}) "
            "RETURN properties(c) AS change, "
            "head([(a:Agent)-[:PROPOSED]->(c) WHERE a.organization_id = $organization_id | properties(a)]) AS agent, "
            "[(c)-[:MODIFIES]->(t:Target) WHERE t.organization_id = $organization_id | properties(t)][..$cap] AS targets, "
            "[(c)-[:ALTERS]->(cl:Claim) WHERE cl.organization_id = $organization_id | cl.claim_type][..$cap] AS claim_types, "
            "[(c)-[:ABOUT]->(p:PromptCluster) WHERE p.organization_id = $organization_id | p.prompt_cluster_id][..$cap] AS clusters, "
            "[(k:Conflict)-[:INVOLVES]->(c) WHERE k.organization_id = $organization_id AND " + _strict("k") + " | "
            "{id: k.conflict_id, type: k.conflict_type, at: k.occurred_at, "
            "others: [(k)-[:INVOLVES]->(o:ChangeSet) WHERE o.organization_id = $organization_id AND o.changeset_id <> c.changeset_id | o.changeset_id], "
            "exps: [(k)-[:INVOLVES|PROTECTS]->(e:Experiment) WHERE e.organization_id = $organization_id | e.experiment_id]}][..$cap] AS conflicts"
        )
        rows = await self._read(org, q, {"changeset_id": ctx.changeset_id, "as_of": _iso(A), "cap": self.config.neighbor_cap})
        if not rows:
            return ctx, False, None, set()
        r = rows[0]
        ch, ag = r["change"], r.get("agent") or {}
        tgs = sorted(r.get("targets") or [], key=lambda t: str(t.get("target_id")))
        upd: dict[str, Any] = {}
        if not ctx.agent_id:
            upd["agent_id"] = ag.get("agent_id") or ch.get("agent_id")
        if not ctx.agent_type:
            upd["agent_type"] = ag.get("agent_type")
        if not ctx.action_type:
            upd["action_type"] = ch.get("action_type")
        if not ctx.target_ids:
            upd["target_ids"] = [str(t["target_id"]) for t in tgs if t.get("target_id")]
        if not ctx.target_type:
            upd["target_type"] = _first([t.get("target_type") for t in tgs])
        if not ctx.claim_types:
            upd["claim_types"] = sorted({str(x) for x in r.get("claim_types") or [] if x})
        if not ctx.prompt_cluster_ids:
            upd["prompt_cluster_ids"] = sorted({str(x) for x in r.get("clusters") or [] if x})
        confs = r.get("conflicts") or []
        if not ctx.conflict_types:
            upd["conflict_types"] = sorted({str(k["type"]) for k in confs if k.get("type")})
        exps = {str(e) for k in confs for e in k.get("exps") or []}
        return ctx.model_copy(update=upd), True, [_conflict(k) for k in confs], exps

    async def _fetch_targets(self, org: str, ctx: ChangeContext, A: datetime) -> list[TargetFacts]:
        if not ctx.target_ids and not ctx.target_keys:
            return []
        cap = self.config.neighbor_cap
        q = (
            "MATCH (t:Target) WHERE t.organization_id = $organization_id "
            "AND (t.target_id IN $target_ids OR t.target_key IN $target_keys) "
            "RETURN properties(t) AS target, "
            "[(c:ChangeSet)-[:MODIFIES]->(t) WHERE c.organization_id = $organization_id AND c.changeset_id <> $self_id AND " + _strict("c") + " | "
            "{id: c.changeset_id, at: c.occurred_at, agent: head([(a:Agent)-[:PROPOSED]->(c) | a.agent_id]), "
            "conflicts: [(k:Conflict)-[:INVOLVES]->(c) WHERE k.organization_id = $organization_id AND " + _strict("k") + " | "
            "{id: k.conflict_id, type: k.conflict_type, at: k.occurred_at}]}][..$cap] AS changes, "
            "[(e:Experiment)-[:MEASURES]->(t) WHERE e.organization_id = $organization_id | properties(e)][..$cap] AS experiments, "
            "[(t)--(n) WHERE n.organization_id = $organization_id AND " + _soft("n") + " AND coalesce(n.changeset_id, '') <> $self_id | "
            "{label: head(labels(n)), id: " + _ID_EXPR + ", at: n.occurred_at}][..$cap] AS neighbors, "
            "[(k:Conflict)-[:AFFECTS]->(t) WHERE k.organization_id = $organization_id AND " + _strict("k") + " | "
            "{id: k.conflict_id, type: k.conflict_type, at: k.occurred_at}][..$cap] AS conflicts "
            "ORDER BY t.target_id LIMIT $cap"
        )
        rows = await self._read(org, q, {
            "target_ids": ctx.target_ids, "target_keys": ctx.target_keys, "self_id": ctx.changeset_id or "",
            "as_of": _iso(A), "cap": cap})
        out = []
        for r in rows:
            t = r["target"]
            out.append(TargetFacts(
                id=str(t["target_id"]), key=t.get("target_key"), protected=bool(t.get("protected")),
                changes=[_change(c) for c in r.get("changes") or []],
                experiments=[e for e in (_experiment(x) for x in r.get("experiments") or []) if e],
                neighbors=[(str(n["label"]), str(n["id"]), to_dt(n.get("at"))) for n in r.get("neighbors") or []
                           if n.get("id") is not None],
                conflicts=[_conflict(k) for k in r.get("conflicts") or []]))
        return out

    async def _fetch_agent(self, org: str, ctx: ChangeContext, A: datetime) -> list[ChangeFact] | None:
        if not ctx.agent_id:
            return None
        q = (
            "MATCH (a:Agent {agent_id: $agent_id, organization_id: $organization_id})-[:PROPOSED]->(c:ChangeSet) "
            "WHERE c.organization_id = $organization_id AND c.changeset_id <> $self_id AND " + _strict("c") + " "
            "RETURN c.changeset_id AS id, c.occurred_at AS at, "
            "[(k:Conflict)-[:INVOLVES]->(c) WHERE k.organization_id = $organization_id AND " + _strict("k") + " | "
            "{id: k.conflict_id, type: k.conflict_type, at: k.occurred_at}] AS conflicts, "
            "[(c)-[:MODIFIES]->(t:Target) | t.target_id] AS targets "
            "ORDER BY datetime(c.occurred_at) DESC, id LIMIT $cap"
        )
        rows = await self._read(org, q, {"agent_id": ctx.agent_id, "self_id": ctx.changeset_id or "",
                                         "as_of": _iso(A), "cap": self.config.max_rows})
        return [_change({**r, "agent": ctx.agent_id}) for r in rows]

    async def _fetch_prompts(self, org: str, ctx: ChangeContext, A: datetime) -> dict[str, list[tuple[str, datetime | None]]] | None:
        if not ctx.prompt_cluster_ids:
            return None
        q = (
            "MATCH (p:PromptCluster) WHERE p.organization_id = $organization_id AND p.prompt_cluster_id IN $ids "
            "RETURN p.prompt_cluster_id AS id, "
            "[(c:ChangeSet)-[:ABOUT]->(p) WHERE c.organization_id = $organization_id AND c.changeset_id <> $self_id AND " + _strict("c") + " | "
            "{id: c.changeset_id, at: c.occurred_at}][..$cap] AS changes ORDER BY id"
        )
        rows = await self._read(org, q, {"ids": ctx.prompt_cluster_ids, "self_id": ctx.changeset_id or "",
                                         "as_of": _iso(A), "cap": self.config.neighbor_cap})
        out: dict[str, list[tuple[str, datetime | None]]] = {pid: [] for pid in ctx.prompt_cluster_ids}
        for r in rows:
            out[str(r["id"])] = [(str(c["id"]), to_dt(c.get("at"))) for c in r.get("changes") or []]
        return out

    async def _fetch_deps(self, org: str, ctx: ChangeContext, A: datetime) -> tuple[int, list[tuple[str, str | None]]]:
        h = _hops(MAX_DEPENDENCY_DEPTH, self.config.hops)
        q = (
            "MATCH (c:ChangeSet {changeset_id: $changeset_id, organization_id: $organization_id}) "
            f"OPTIONAL MATCH p=(c)-[:DERIVED_FROM*1..{h}]->(pre:ChangeSet) "
            "WHERE all(x IN nodes(p) WHERE x.organization_id = $organization_id AND " + _soft("x") + ") "
            "RETURN length(p) AS depth, properties(pre) AS pre LIMIT $cap"
        )
        rows = await self._read(org, q, {"changeset_id": ctx.changeset_id, "as_of": _iso(A), "cap": self.config.max_rows})
        depth = max([int(r["depth"]) for r in rows if r.get("depth") is not None] or [0])
        preds: dict[str, str | None] = {}
        for r in rows:
            pre = r.get("pre")
            if pre and pre.get("changeset_id"):
                preds[str(pre["changeset_id"])] = pre.get("status")
        return depth, sorted(preds.items())

    async def _fetch_path(self, org: str, ctx: ChangeContext, A: datetime) -> int | None:
        if not ctx.target_ids and not ctx.target_keys:
            return None
        h = _hops(MAX_PATH_HOPS, self.config.hops)
        q = (
            "MATCH (t:Target) WHERE t.organization_id = $organization_id "
            "AND (t.target_id IN $target_ids OR t.target_key IN $target_keys) "
            "MATCH (e:Experiment {organization_id: $organization_id}) "
            "WHERE (e.started_at IS NULL OR datetime(e.started_at) <= datetime($as_of)) "
            "AND (e.ends_at IS NULL OR datetime(e.ends_at) > datetime($as_of)) "
            f"MATCH p=shortestPath((t)-[*1..{h}]-(e)) "
            "WHERE all(x IN nodes(p) WHERE x.organization_id = $organization_id AND coalesce(x.changeset_id, '') <> $self_id "
            "AND " + _soft("x") + ") "
            "RETURN length(p) AS hops, properties(e) AS exp ORDER BY hops LIMIT $cap"
        )
        rows = await self._read(org, q, {"target_ids": ctx.target_ids, "target_keys": ctx.target_keys,
                                         "self_id": ctx.changeset_id or "", "as_of": _iso(A), "cap": 50})
        hops = [int(r["hops"]) for r in rows
                if (e := _experiment(r.get("exp"))) is not None and experiment_active(e, A)]
        return min(hops) if hops else None

    async def _fetch_candidates(self, org: str, ctx: ChangeContext, A: datetime) -> list[Candidate]:
        q = (
            "MATCH (d:Decision)-[:DECIDES_ON]->(c:ChangeSet) "
            "WHERE d.organization_id = $organization_id AND c.organization_id = $organization_id "
            "AND c.changeset_id <> $self_id AND " + _strict("d") + " AND " + _strict("c") + " "
            "WITH d, c ORDER BY datetime(d.occurred_at) DESC, d.decision_id LIMIT $cap "
            "RETURN d.decision_id AS decision_id, d.decision AS decision, d.occurred_at AS decision_at, "
            "c.changeset_id AS id, c.occurred_at AS at, c.action_type AS action_type, "
            "head([(a:Agent)-[:PROPOSED]->(c) | a.agent_type]) AS agent_type, "
            "[(c)-[:MODIFIES]->(t:Target) | t.target_type] AS target_types, "
            "[(c)-[:ALTERS]->(cl:Claim) | cl.claim_type] AS claim_types, "
            "[(c)-[:ABOUT]->(p:PromptCluster) | p.prompt_cluster_id] AS clusters, "
            "[(k:Conflict)-[:INVOLVES]->(c) WHERE " + _strict("k") + " | k.conflict_type] AS conflict_types, "
            "[(k:Conflict)-[:INVOLVES]->(c) | [(k)-[:INVOLVES|PROTECTS]->(e:Experiment) | e.experiment_id]] AS conflict_exps, "
            "[(c)-[:MODIFIES]->(:Target)<-[:MEASURES]-(e2:Experiment) | e2.experiment_id] AS target_exps, "
            "head([(d)-[:PRODUCED|EXECUTED_AS|OBSERVED*1..3]->(o:Outcome) WHERE " + _strict("o") + " | "
            "{label: coalesce(o.outcome_label, o.label, o.outcome), reward: o.reward}]) AS outcome"
        )
        rows = await self._read(org, q, {"self_id": ctx.changeset_id or "", "as_of": _iso(A),
                                         "cap": self.config.similar_candidates})
        latest: dict[str, dict[str, Any]] = {}
        for r in rows:  # one (latest) decision per ChangeSet
            cur = latest.get(r["id"])
            if cur is None or (to_dt(r["decision_at"]) or A) >= (to_dt(cur["decision_at"]) or A):
                latest[r["id"]] = r
        out = []
        for r in latest.values():
            exps = {str(e) for sub in r.get("conflict_exps") or [] for e in sub if e} | {str(e) for e in r.get("target_exps") or [] if e}
            sig = ContextSignature(
                agent_type=r.get("agent_type"), action_type=r.get("action_type"),
                target_type=_first(r.get("target_types") or []),
                claim_types=frozenset(str(x) for x in r.get("claim_types") or [] if x),
                conflict_types=frozenset(str(x) for x in r.get("conflict_types") or [] if x),
                experiments=frozenset(exps),
                prompt_clusters=frozenset(str(x) for x in r.get("clusters") or [] if x))
            o = r.get("outcome") or {}
            reward = o.get("reward")
            out.append(Candidate(
                changeset_id=str(r["id"]), signature=sig, decision=(str(r["decision"]).upper() if r.get("decision") else None),
                decision_id=r.get("decision_id"), occurred_at=to_dt(r.get("at")),
                outcome=(str(o["label"]) if o.get("label") is not None else None),
                reward=float(reward) if isinstance(reward, int | float) else None))
        out.sort(key=lambda c: c.changeset_id)
        return out

    @staticmethod
    def _signature(ctx: ChangeContext, found: bool, targets: list[TargetFacts], conflict_exps: set[str],
                   A: datetime) -> ContextSignature:
        exps = set(ctx.experiment_ids) | conflict_exps | {e.id for t in targets for e in t.experiments}
        known: set[str] = set()
        if found or ctx.claim_types:
            known.add("claim_types")
        if found or ctx.conflict_types:
            known.add("conflict_types")
        if targets or ctx.experiment_ids or conflict_exps:
            known.add("experiments")
        if ctx.prompt_cluster_ids:
            known.add("prompt_clusters")
        return ContextSignature(
            agent_type=ctx.agent_type, action_type=ctx.action_type, target_type=ctx.target_type,
            claim_types=frozenset(ctx.claim_types), conflict_types=frozenset(ctx.conflict_types),
            experiments=frozenset(exps), prompt_clusters=frozenset(ctx.prompt_cluster_ids), known_sets=frozenset(known))

    # ------------------------------------------------------------- Q3 / Q7 similarity
    async def get_similar_contexts(self, organization_id: Any, changeset_id: str | None = None, *,
                                   context: ChangeContext | None = None, as_of: datetime | None = None,
                                   min_score: float = 0.5, top_k: int = 25) -> SimilarContexts:
        org = require_org(organization_id)
        A = self._as_of(as_of)
        ctx = (context or ChangeContext()).model_copy(update={"changeset_id": changeset_id or (context.changeset_id if context else None)})
        ctx, found, _confs, cexps = await self._resolve(org, ctx, A)
        targets = await self._fetch_targets(org, ctx, A)
        cands = await self._fetch_candidates(org, ctx, A)
        return self._similar_result(ctx, found, targets, cexps, cands, A, min_score, top_k)

    def _similar_result(self, ctx: ChangeContext, found: bool, targets: list[TargetFacts], cexps: set[str],
                        cands: list[Candidate], A: datetime, min_score: float, top_k: int) -> SimilarContexts:
        sig = self._signature(ctx, found, targets, cexps, A)
        ranked = rank_similar(sig, cands, min_score=min_score, top_k=top_k)
        rates = decision_rates(ranked)
        return SimilarContexts(
            changeset_id=ctx.changeset_id, contexts=ranked, candidates_considered=len(cands),
            by_decision=stats_by_decision(ranked), allow_rate=rates["allow"], block_rate=rates["block"],
            review_rate=rates["review"], generated_at=datetime.now(UTC), as_of=A,
            truncated=len(cands) >= self.config.similar_candidates)

    # ------------------------------------------------------------- features
    async def collect_facts(self, organization_id: Any, ctx: ChangeContext, A: datetime
                            ) -> tuple[ContextFacts, ChangeContext, SimilarContexts]:
        org = require_org(organization_id)
        ctx, found, confs, cexps = await self._resolve(org, ctx, A)
        coros: list[Awaitable[Any]] = [
            self._fetch_targets(org, ctx, A), self._fetch_agent(org, ctx, A), self._fetch_prompts(org, ctx, A),
            self._fetch_path(org, ctx, A), self._fetch_candidates(org, ctx, A)]
        if found:
            coros.append(self._fetch_deps(org, ctx, A))
        res = await asyncio.gather(*coros)
        targets, agent_changes, prompts, path, cands = res[:5]
        depth, preds = res[5] if found else (None, [])
        similar = self._similar_result(ctx, found, targets, cexps, cands, A, 0.5, 25)
        facts = ContextFacts(
            organization_id=org, changeset_id=ctx.changeset_id, changeset_found=found, agent_id=ctx.agent_id,
            prompt_cluster_ids=list(ctx.prompt_cluster_ids), targets=targets, agent_changes=agent_changes,
            own_conflicts=confs, prompt_overlap=prompts, dependency_depth=depth, predecessors=preds,
            path_hops=path, path_checked=bool(targets),
            similar_decisions=[c.decision for c in similar.contexts if c.decision], similar_checked=True)
        return facts, ctx, similar

    def features_from_facts(self, facts: ContextFacts, ctx: ChangeContext, A: datetime, *, stale: bool = False,
                            reason: str | None = None) -> GraphFeatures:
        values = compute_features(facts, A)
        vec = encode_vector(values)
        ckey = {"changeset_id": ctx.changeset_id, "agent_id": ctx.agent_id, "action_type": ctx.action_type,
                "target_ids": sorted(t.id for t in facts.targets), "prompt_cluster_ids": sorted(ctx.prompt_cluster_ids)}
        return GraphFeatures(
            vector=vec, names=list(VECTOR_NAMES), version=FEATURE_VERSION, snapshot_time=A, computed_at=datetime.now(UTC),
            context_hash=context_hash(facts.organization_id, A, values, ckey), stale=stale, unavailable=False,
            source="graph", reason=reason, features={n: (0.0 if values[n] is None else values[n]) for n in FEATURE_NAMES},  # type: ignore[misc]
            missing=[n for n in FEATURE_NAMES if values[n] is None], organization_id=facts.organization_id,
            changeset_id=ctx.changeset_id)

    async def get_graph_features(self, organization_id: Any, changeset_id: str | None = None, *,
                                 context: ChangeContext | None = None, as_of: datetime | None = None,
                                 staleness: StalenessCheck | None = None, use_cache: bool = True,
                                 persist: bool | None = None) -> GraphFeatures:
        """graph_context_v1 for a ChangeSet or an explicit context. NEVER raises: on any failure returns
        `GraphFeatures(stale=True, unavailable=True, source='unavailable', reason=...)` so the policy falls back to
        BaselinePolicy."""
        explicit_as_of = as_of is not None
        try:
            org = require_org(organization_id)
        except OrganizationScopeError:
            return _unavailable(None, changeset_id, "organization_required")
        try:
            A = self._as_of(as_of)
            ctx = (context or ChangeContext()).model_copy(
                update={"changeset_id": changeset_id or (context.changeset_id if context else None)})
            if not ctx.changeset_id and not (ctx.target_ids or ctx.target_keys or ctx.agent_id):
                return _unavailable(org, None, "empty_context")
            ckey = FeatureCache.key(org, ctx.key(), A, FEATURE_VERSION)
            if use_cache and explicit_as_of and (hit := self.cache.get(ckey)) is not None:
                hit.source = "cache"
                return hit
            check = staleness or (lambda o: default_staleness_check(o, self.config.stale_lag_s))
            stale, reason = await check(org)
            if stale:
                return _unavailable(org, ctx.changeset_id, reason or "graph_context_stale", stale=True, snapshot=A)
            facts, ctx2, _sim = await self.collect_facts(org, ctx, A)
            if ctx2.changeset_id and not facts.changeset_found:
                gf = self.features_from_facts(facts, ctx2, A, stale=True, reason="changeset_not_projected")
                return gf
            gf = self.features_from_facts(facts, ctx2, A)
            if use_cache and explicit_as_of:
                self.cache.put(ckey, gf)
            if (self.config.persist_features if persist is None else persist) and gf.changeset_id:
                await persist_features(self.client, gf)
            return gf
        except GraphUnavailable as exc:
            return _unavailable(org, changeset_id, f"graph_unavailable:{getattr(exc, 'state', 'DEGRADED')}")
        except GraphError as exc:
            log.warning("graph.features_failed", error=type(exc).__name__)
            return _unavailable(org, changeset_id, f"graph_error:{type(exc).__name__}")
        except Exception as exc:  # noqa: BLE001 - the policy path must never see an exception
            log.warning("graph.features_failed", error=type(exc).__name__)
            return _unavailable(org, changeset_id, f"unexpected:{type(exc).__name__}")


def _unavailable(org: str | None, changeset_id: str | None, reason: str, *, stale: bool = True,
                 snapshot: datetime | None = None) -> GraphFeatures:
    return GraphFeatures(
        vector=[], names=list(VECTOR_NAMES), version=FEATURE_VERSION, snapshot_time=snapshot, computed_at=datetime.now(UTC),
        stale=stale, unavailable=True, source="unavailable", reason=reason, organization_id=org, changeset_id=changeset_id)


async def get_graph_features(organization_id: Any, changeset_id: str | None = None, context: ChangeContext | None = None,
                             *, as_of: datetime | None = None, repo: GraphRepository | None = None,
                             staleness: StalenessCheck | None = None) -> GraphFeatures:
    """Module-level entry point for N5: `get_graph_features(organization_id, changeset_id|context)`. Never raises."""
    return await (repo or GraphRepository()).get_graph_features(
        organization_id, changeset_id, context=context, as_of=as_of, staleness=staleness)


__all__ = ["GraphRepository", "QueryConfig", "get_graph_features"]
