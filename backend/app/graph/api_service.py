"""Thin service between /api/graph/* routes and GraphRepository (N3).

Responsibilities: organization resolution (Postgres is the authority; no cross-org reads), short TTL cache of read
results, an overall timeout, honest degradation (Neo4j down/slow => `source: "unavailable"`, empty collections and a
`reason`; never fake data, never an exception for read views) and the pure highlight-path derivation.
The browser never sees Cypher: only normalized views built by the repository.
"""
from __future__ import annotations

import asyncio
import time
import uuid
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, TypeVar

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.domain.errors import GraphOrgRequired, OrganizationNotFound
from app.graph.client import GraphClient, get_graph_client
from app.graph.errors import GraphError, GraphUnavailable
from app.graph.health import graph_health
from app.graph.queries import GraphRepository
from app.graph.results import GraphView
from app.schemas.graph import (
    AgentConflictPairOut,
    AgentConflictsResponse,
    ContextResponse,
    ContradictedClaimOut,
    ContradictedClaimsResponse,
    DecisionStatsOut,
    EdgeOut,
    ExplanationResponse,
    GraphHealthResponse,
    HighlightPaths,
    LastProjectedEvent,
    LineageResponse,
    NodeOut,
    ProjectionLag,
    SimilarContextOut,
    StatementOut,
    TargetContentionResponse,
    TargetContentionRowOut,
)

log = structlog.get_logger()
T = TypeVar("T")

SINCE = {"1h": timedelta(hours=1), "24h": timedelta(hours=24), "7d": timedelta(days=7)}
CACHE_TTL_S = 5.0
CACHE_MAX = 256
DEPTH_MIN, DEPTH_MAX = 1, 4
LIMIT_MAX = 100

# stages of the two highlighted chains (label sets; Execution has no node of its own: Approval/Experiment stand in)
DECISION_UP = [{"Conflict"}, {"ChangeSet"}, {"Event"}, {"AgentRun"}, {"Agent"}]
OUTCOME_DOWN = [{"Approval", "Experiment"}, {"Experiment"}, {"Observation"}, {"Outcome"}, {"PolicyVersion", "PolicyDecision"}]
_PATH_BLOCK = {"Target", "PromptCluster", "Organization", "Claim"}  # never an interior node of a highlighted chain


def clamp_depth(depth: int) -> int:
    return max(DEPTH_MIN, min(int(depth), DEPTH_MAX))


def since_dt(since: str | None, now: datetime | None = None) -> datetime | None:
    if since is None:
        return None
    return (now or datetime.now(UTC)) - SINCE[since]


# --------------------------------------------------------------------------------------------- highlight paths
def _adjacency(view: GraphView) -> dict[str, set[str]]:
    adj: dict[str, set[str]] = {n.id: set() for n in view.nodes}
    for e in view.edges:
        if e.source in adj and e.target in adj:
            adj[e.source].add(e.target)
            adj[e.target].add(e.source)
    return adj


def _bfs(adj: dict[str, set[str]], labels: dict[str, str], start: str, want: set[str], blocked: set[str],
         max_depth: int = 3) -> list[str] | None:
    """Shortest real path start -> nearest node whose label is in `want` (deterministic: sorted neighbours)."""
    q: deque[tuple[str, list[str]]] = deque([(start, [start])])
    seen = {start} | blocked
    while q:
        cur, path = q.popleft()
        if len(path) > max_depth + 1:
            continue
        for nb in sorted(adj.get(cur, ())):
            if nb in seen:
                continue
            seen.add(nb)
            if labels[nb] in want:
                return [*path, nb]
            if labels[nb] not in _PATH_BLOCK:
                q.append((nb, [*path, nb]))
    return None


def _chain(adj: dict[str, set[str]], labels: dict[str, str], start: str, stages: list[set[str]]) -> list[str]:
    path = [start]
    for want in stages:
        step = _bfs(adj, labels, path[-1], want, set(path[:-1]))
        if step is not None:
            path.extend(step[1:])
    return path


def highlight_paths(view: GraphView) -> HighlightPaths:
    """decision_path (Agent->Run->Event->Change->Conflict->Decision) and outcome_path (Decision->Execution->
    Observation->Outcome->Policy update) for the latest Decision in the view that touches the focus. Pure: every id is
    a node of `view` joined by real edges; a missing stage is skipped, never invented."""
    decisions = [n for n in view.nodes if n.label == "Decision"]
    if not decisions:
        return HighlightPaths()
    labels = {n.id: n.label for n in view.nodes}
    adj = _adjacency(view)
    focus = view.focus_id

    def key(n: Any) -> tuple[str, str]:
        return (str(n.props.get("occurred_at") or ""), n.id)

    linked = [d for d in decisions if focus is None or focus in adj.get(d.id, ()) or d.id == focus] or decisions
    d = sorted(linked, key=key)[-1]
    up = _chain(adj, labels, d.id, DECISION_UP)
    down = _chain(adj, labels, d.id, OUTCOME_DOWN)
    up_path = list(reversed(up)) if len(up) > 1 else []
    down_path = down if len(down) > 1 else []
    return HighlightPaths(
        decision_id=d.id, decision_path=up_path, outcome_path=down_path,
        decision_path_labels=[labels[i] for i in up_path], outcome_path_labels=[labels[i] for i in down_path])


# --------------------------------------------------------------------------------------------- service
class GraphApiService:
    def __init__(self, repo: GraphRepository | None = None, client: GraphClient | None = None, *,
                 cache_ttl_s: float = CACHE_TTL_S, timeout_s: float | None = None, validate_org: bool = True):
        self._repo = repo
        self._client = client
        self.cache_ttl_s = cache_ttl_s
        self._timeout = timeout_s
        self.validate_org = validate_org
        self._cache: dict[tuple, tuple[float, Any]] = {}

    # -- plumbing ------------------------------------------------------------------------------------------
    @property
    def repo(self) -> GraphRepository:
        return self._repo or GraphRepository()

    @property
    def client(self) -> GraphClient:
        return self._client or (self._repo.client if self._repo else get_graph_client())

    @property
    def timeout_s(self) -> float:
        return self._timeout if self._timeout is not None else max(3.0, get_settings().neo4j_query_timeout_s * 2)

    def _cache_get(self, key: tuple) -> Any | None:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < self.cache_ttl_s:
            return hit[1]
        self._cache.pop(key, None)
        return None

    def _cache_put(self, key: tuple, value: Any) -> None:
        if self.cache_ttl_s <= 0:
            return
        if len(self._cache) >= CACHE_MAX:
            self._cache.pop(min(self._cache, key=lambda k: self._cache[k][0]))
        self._cache[key] = (time.monotonic(), value)

    def clear_cache(self) -> None:
        self._cache.clear()

    async def _call(self, fn: Callable[[], Awaitable[T]]) -> tuple[T | None, str | None]:
        """Run a repository call. Returns (result, None) or (None, reason) when the graph cannot answer."""
        try:
            return await asyncio.wait_for(fn(), timeout=self.timeout_s), None
        except GraphUnavailable as exc:
            return None, f"neo4j_{getattr(exc, 'state', 'DEGRADED').lower()}"
        except TimeoutError:
            return None, "neo4j_timeout"
        except GraphError as exc:
            log.warning("graph_api.query_failed", error=type(exc).__name__)
            return None, f"graph_error_{type(exc).__name__.lower()}"

    async def _stale(self, session: AsyncSession) -> tuple[bool, dict[str, Any] | None]:
        """Projection staleness from Postgres (never needs Neo4j). Unknown => stale."""
        try:
            from app.graph.outbox import lag

            info = await lag(session)
            return bool(info["stale"]), info
        except Exception as exc:  # noqa: BLE001 - table missing / DB hiccup
            log.info("graph_api.lag_unknown", error=type(exc).__name__)
            return True, None

    async def _neo4j_ready(self) -> str | None:
        """None when READY, else a reason. Uses the cached health probe (cheap)."""
        try:
            h = await asyncio.wait_for(graph_health(self.client), timeout=self.timeout_s)
        except Exception:  # noqa: BLE001
            return "neo4j_degraded"
        return None if h["state"] == "READY" else f"neo4j_{h['state'].lower()}"

    # -- organization resolution --------------------------------------------------------------------------
    async def resolve_org(self, session: AsyncSession, org_id: uuid.UUID | None, *,
                          changeset_id: uuid.UUID | None = None, experiment_id: uuid.UUID | None = None) -> str:
        """Organization the request is scoped to. An id that belongs to another organization is reported as not found
        (never leaked). Without `org_id`: derived from the entity, else the only organization, else GRAPH_ORG_REQUIRED."""
        from app.models.changeguard import ChangeSet
        from app.models.core import Incident, Organization
        from app.models.interventions import Experiment

        if not self.validate_org:
            if org_id is None:
                raise GraphOrgRequired("org_id is required", {"field": "org_id"})
            return str(org_id)
        owner: uuid.UUID | None = None
        if changeset_id is not None:
            owner = (await session.execute(select(ChangeSet.org_id).where(ChangeSet.id == changeset_id))).scalar()
            if owner is None:
                raise OrganizationNotFound("change set not found", {"changeset_id": str(changeset_id)})
        elif experiment_id is not None:
            owner = (await session.execute(
                select(Incident.org_id).join(Experiment, Experiment.incident_id == Incident.id)
                .where(Experiment.id == experiment_id))).scalar()
            if owner is None:
                raise OrganizationNotFound("experiment not found", {"experiment_id": str(experiment_id)})
        if owner is not None:
            if org_id is not None and org_id != owner:
                what = {"changeset_id": str(changeset_id)} if changeset_id else {"experiment_id": str(experiment_id)}
                raise OrganizationNotFound("not found in this organization", what)
            return str(owner)
        if org_id is not None:
            if await session.get(Organization, org_id) is None:
                raise OrganizationNotFound("organization not found", {"org_id": str(org_id)})
            return str(org_id)
        ids = (await session.execute(select(Organization.id).limit(2))).scalars().all()
        count = (await session.execute(select(func.count()).select_from(Organization))).scalar_one() if len(ids) > 1 else len(ids)
        if count == 1:
            return str(ids[0])
        raise GraphOrgRequired("org_id is required", {"field": "org_id", "organizations": int(count)})

    # -- lineage ------------------------------------------------------------------------------------------
    @staticmethod
    def _lineage_out(org: str, view: GraphView | None, reason: str | None, stale: bool, with_paths: bool) -> LineageResponse:
        now = datetime.now(UTC)
        if view is None:
            return LineageResponse(organization_id=org, generated_at=now, source="unavailable", graph_stale=stale,
                                   reason=reason)
        out = LineageResponse(
            organization_id=org, generated_at=view.generated_at, source="neo4j", graph_stale=stale,
            reason=reason if reason else (None if view.found else "not_projected"),
            nodes=[NodeOut(**n.model_dump()) for n in view.nodes], edges=[EdgeOut(**e.model_dump()) for e in view.edges],
            focus_id=view.focus_id, max_hops=view.max_hops, truncated=view.truncated, found=view.found,
            as_of=view.as_of)
        if with_paths:
            out.highlight_paths = highlight_paths(view)
        return out

    async def change_lineage(self, session: AsyncSession, org: str, changeset_id: uuid.UUID, depth: int) -> LineageResponse:
        depth = clamp_depth(depth)
        stale, _ = await self._stale(session)
        key = ("change_lineage", org, str(changeset_id), depth)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return self._lineage_out(org, None, why, stale, True)
        view, reason = await self._call(lambda: self.repo.get_change_lineage(org, str(changeset_id), max_hops=depth))
        out = self._lineage_out(org, view, reason, stale, True)
        if out.source == "neo4j" and out.found:
            self._cache_put(key, out)
        return out

    async def experiment_lineage(self, session: AsyncSession, org: str, experiment_id: uuid.UUID, depth: int) -> LineageResponse:
        depth = clamp_depth(depth)
        stale, _ = await self._stale(session)
        key = ("experiment_lineage", org, str(experiment_id), depth)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return self._lineage_out(org, None, why, stale, True)
        view, reason = await self._call(lambda: self.repo.get_experiment_lineage(org, str(experiment_id), max_hops=depth))
        out = self._lineage_out(org, view, reason, stale, True)
        if out.source == "neo4j" and out.found:
            self._cache_put(key, out)
        return out

    # -- explanation --------------------------------------------------------------------------------------
    async def explanation(self, session: AsyncSession, org: str, changeset_id: uuid.UUID) -> ExplanationResponse:
        cid = str(changeset_id)
        stale, _ = await self._stale(session)
        base = {"organization_id": org, "changeset_id": cid, "graph_stale": stale}
        key = ("explanation", org, cid)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return ExplanationResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=why)
        res, reason = await self._call(lambda: self.repo.get_decision_explanation(org, cid))
        if res is None:
            return ExplanationResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=reason)
        statements = [StatementOut(text=s.text, node_ids=list(s.node_ids), edge_ids=list(s.edges)) for s in res.statements]
        out = ExplanationResponse(
            **base, generated_at=res.generated_at, source="neo4j", found=res.found, decision=res.decision,
            decision_id=res.decision_id, text=res.text, statements=statements,
            reason=None if res.found else "not_projected")
        if out.found:
            self._cache_put(key, out)
        return out

    # -- context ------------------------------------------------------------------------------------------
    async def context(self, session: AsyncSession, org: str, changeset_id: uuid.UUID) -> ContextResponse:
        cid = str(changeset_id)
        stale, _ = await self._stale(session)
        base = {"organization_id": org, "changeset_id": cid}
        if (why := await self._neo4j_ready()) is not None:
            return ContextResponse(**base, generated_at=datetime.now(UTC), source="unavailable", graph_stale=stale,
                                   reason=why)
        key = ("context", org, cid)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        repo = self.repo
        feats, _ = await self._call(lambda: repo.get_graph_features(org, cid))
        if feats is None:  # get_graph_features never raises; only a timeout lands here
            return ContextResponse(**base, generated_at=datetime.now(UTC), source="unavailable", graph_stale=stale,
                                   reason="neo4j_timeout")
        reason = feats.reason
        source = "neo4j"
        if feats.unavailable and (reason or "").startswith(("graph_unavailable", "graph_error", "unexpected")):
            source = "unavailable"
        out = ContextResponse(
            **base, generated_at=feats.computed_at or datetime.now(UTC), source=source, graph_stale=stale or feats.stale,
            reason=reason, version=feats.version, snapshot_time=feats.snapshot_time, stale=feats.stale or feats.unavailable,
            names=list(feats.names), vector=list(feats.vector), features=dict(feats.features),
            missing=list(feats.missing), context_hash=feats.context_hash)
        if source == "neo4j":
            sim, why2 = await self._call(lambda: repo.get_similar_contexts(org, cid))
            if sim is not None:
                out.similar_contexts = [SimilarContextOut(**c.model_dump()) for c in sim.contexts]
                out.similar_by_decision = [DecisionStatsOut(**d.model_dump()) for d in sim.by_decision]
                out.candidates_considered = sim.candidates_considered
            elif why2:
                out.reason = out.reason or f"similar_contexts_{why2}"
        if out.source == "neo4j" and not out.stale:
            self._cache_put(key, out)
        return out

    # -- summaries ----------------------------------------------------------------------------------------
    async def agent_conflicts(self, session: AsyncSession, org: str, agent_id: str | None, since: str | None
                              ) -> AgentConflictsResponse:
        stale, _ = await self._stale(session)
        base = {"organization_id": org, "agent_id": agent_id, "since": since, "graph_stale": stale}
        key = ("agent_conflicts", org, agent_id, since)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return AgentConflictsResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=why)
        res, reason = await self._call(lambda: self.repo.get_agent_conflicts(org, agent_id=agent_id, since=since_dt(since)))
        if res is None:
            return AgentConflictsResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=reason)
        out = AgentConflictsResponse(**base, generated_at=res.generated_at, source="neo4j", truncated=res.truncated,
                                     pairs=[AgentConflictPairOut(**p.model_dump()) for p in res.pairs])
        self._cache_put(key, out)
        return out

    async def target_contention(self, session: AsyncSession, org: str, since: str | None, limit: int
                                ) -> TargetContentionResponse:
        limit = max(1, min(int(limit), LIMIT_MAX))
        stale, _ = await self._stale(session)
        base = {"organization_id": org, "since": since, "limit": limit, "graph_stale": stale}
        key = ("contention", org, since, limit)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return TargetContentionResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=why)
        res, reason = await self._call(lambda: self.repo.get_target_contention(org, since=since_dt(since), limit=limit))
        if res is None:
            return TargetContentionResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=reason)
        out = TargetContentionResponse(**base, generated_at=res.generated_at, source="neo4j", truncated=res.truncated,
                                       rows=[TargetContentionRowOut(**r.model_dump()) for r in res.rows])
        self._cache_put(key, out)
        return out

    async def contradicted_claims(self, session: AsyncSession, org: str, since: str | None, min_count: int, limit: int
                                  ) -> ContradictedClaimsResponse:
        limit = max(1, min(int(limit), LIMIT_MAX))
        min_count = max(1, min(int(min_count), 50))
        stale, _ = await self._stale(session)
        base = {"organization_id": org, "since": since, "min_count": min_count, "limit": limit, "graph_stale": stale}
        key = ("claims", org, since, min_count, limit)
        if (hit := self._cache_get(key)) is not None:
            return hit.model_copy(update={"graph_stale": stale})
        if (why := await self._neo4j_ready()) is not None:
            return ContradictedClaimsResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=why)
        res, reason = await self._call(lambda: self.repo.get_contradicted_claims(
            org, min_count=min_count, since=since_dt(since), limit=limit))
        if res is None:
            return ContradictedClaimsResponse(**base, generated_at=datetime.now(UTC), source="unavailable", reason=reason)
        out = ContradictedClaimsResponse(**base, generated_at=res.generated_at, source="neo4j", truncated=res.truncated,
                                         claims=[ContradictedClaimOut(**c.model_dump()) for c in res.claims])
        self._cache_put(key, out)
        return out

    # -- health -------------------------------------------------------------------------------------------
    async def health(self, session: AsyncSession, org: str | None) -> GraphHealthResponse:
        from app.graph.outbox import graph_lag
        from app.models.graph_outbox import GraphOutbox

        try:
            h = await asyncio.wait_for(graph_health(self.client, force=False), timeout=self.timeout_s)
        except Exception:  # noqa: BLE001
            h = {"state": "DEGRADED", "latency_ms": None, "error": "health_probe_failed", "database": None}
        proj = ProjectionLag()
        stale = True
        try:
            lag = await graph_lag(session)
            proj = ProjectionLag(**{k: lag.get(k) for k in ProjectionLag.model_fields if lag.get(k) is not None})
            stale = bool(lag.get("stale"))
        except Exception as exc:  # noqa: BLE001
            log.info("graph_api.health_lag_unknown", error=type(exc).__name__)
        last = None
        try:
            q = select(GraphOutbox.id, GraphOutbox.event_type, GraphOutbox.organization_id, GraphOutbox.processed_at)\
                .where(GraphOutbox.processed_at.is_not(None))
            if org:
                q = q.where(GraphOutbox.organization_id == org)
            row = (await session.execute(q.order_by(GraphOutbox.processed_at.desc()).limit(1))).first()
            if row:
                last = LastProjectedEvent(event_id=str(row[0]), event_type=row[1], organization_id=row[2], processed_at=row[3])
        except Exception as exc:  # noqa: BLE001
            log.info("graph_api.health_last_unknown", error=type(exc).__name__)
        counts: dict[str, int] = {}
        reason = None
        ready = h["state"] == "READY"
        if not ready:
            reason = f"neo4j_{h['state'].lower()}"
        elif org:
            rows, why = await self._call(lambda: self.client.scoped_read(
                org, "MATCH (n) WHERE n.organization_id = $organization_id UNWIND labels(n) AS l "
                "RETURN l AS label, count(*) AS n ORDER BY label", {}, timeout=self.timeout_s))
            if rows is not None:
                counts = {r["label"]: int(r["n"]) for r in rows}
            else:
                reason = why
        return GraphHealthResponse(
            generated_at=datetime.now(UTC), source="neo4j" if ready and reason is None else "unavailable",
            graph_stale=stale, reason=reason, organization_id=org, neo4j_state=h["state"],
            neo4j_latency_ms=h.get("latency_ms"), neo4j_error=h.get("error"), database=h.get("database"),
            projection=proj, last_projected_event=last, node_counts=counts)


_service: GraphApiService | None = None


def get_graph_api_service() -> GraphApiService:
    global _service
    if _service is None:
        _service = GraphApiService()
    return _service
