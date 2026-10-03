"""Graph API (N6): read-only, organization-scoped views over the Neo4j projection. No Cypher is accepted or returned.
Neo4j down/slow -> 200 with `source: "unavailable"` (honest degraded state); other routes are never affected."""
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.api.deps import SessionDep
from app.graph.api_service import GraphApiService, get_graph_api_service
from app.schemas.graph import (
    AgentConflictsResponse,
    ContextResponse,
    ContradictedClaimsResponse,
    ExplanationResponse,
    GraphHealthResponse,
    LineageResponse,
    Since,
    TargetContentionResponse,
)

router = APIRouter(prefix="/api/graph", tags=["graph"])

Svc = Annotated[GraphApiService, Depends(get_graph_api_service)]
OrgQ = Annotated[uuid.UUID | None, Query(description="Organization scope. Optional only when it can be derived "
                                                    "from the entity or exactly one organization exists.")]
SinceQ = Annotated[Since | None, Query(description="Time filter: 1h | 24h | 7d (default: all history)")]
DepthQ = Annotated[int, Query(ge=1, le=4, description="Traversal depth in hops (1..4)")]


@router.get("/changes/{changeset_id}/lineage", response_model=LineageResponse)
async def change_lineage(changeset_id: uuid.UUID, session: SessionDep, svc: Svc, org_id: OrgQ = None, depth: DepthQ = 4):
    """Normalized {nodes, edges, focus_id} around a ChangeSet (upstream Agent/Run/Event, downstream Conflict/Decision/
    Experiment/Outcome) + `highlight_paths` (decision path and outcome path as arrays of real node ids)."""
    org = await svc.resolve_org(session, org_id, changeset_id=changeset_id)
    return await svc.change_lineage(session, org, changeset_id, depth)


@router.get("/changes/{changeset_id}/context", response_model=ContextResponse)
async def change_context(changeset_id: uuid.UUID, session: SessionDep, svc: Svc, org_id: OrgQ = None):
    """graph_context_v1 features (names, vector, version, snapshot_time, stale) + similar historical contexts."""
    org = await svc.resolve_org(session, org_id, changeset_id=changeset_id)
    return await svc.context(session, org, changeset_id)


@router.get("/changes/{changeset_id}/explanation", response_model=ExplanationResponse)
async def change_explanation(changeset_id: uuid.UUID, session: SessionDep, svc: Svc, org_id: OrgQ = None):
    """Deterministic 'why blocked/delayed' built from real nodes/edges; every statement lists its supporting ids."""
    org = await svc.resolve_org(session, org_id, changeset_id=changeset_id)
    return await svc.explanation(session, org, changeset_id)


@router.get("/experiments/{experiment_id}/lineage", response_model=LineageResponse)
async def experiment_lineage(experiment_id: uuid.UUID, session: SessionDep, svc: Svc, org_id: OrgQ = None,
                             depth: DepthQ = 3):
    org = await svc.resolve_org(session, org_id, experiment_id=experiment_id)
    return await svc.experiment_lineage(session, org, experiment_id, depth)


@router.get("/agents/{agent_id}/conflicts", response_model=AgentConflictsResponse)
async def agent_conflicts(agent_id: str, session: SessionDep,
                          svc: Svc, org_id: OrgQ = None, since: SinceQ = None):
    """Agent pairs whose changes collided with `agent_id` (graph agent id = business key of the Agent node)."""
    org = await svc.resolve_org(session, org_id)
    return await svc.agent_conflicts(session, org, agent_id[:256], since)


@router.get("/targets/contention", response_model=TargetContentionResponse)
async def target_contention(session: SessionDep, svc: Svc, org_id: OrgQ = None, since: SinceQ = None,
                            limit: Annotated[int, Query(ge=1, le=100)] = 20):
    org = await svc.resolve_org(session, org_id)
    return await svc.target_contention(session, org, since, limit)


@router.get("/claims/contradicted", response_model=ContradictedClaimsResponse)
async def contradicted_claims(session: SessionDep, svc: Svc, org_id: OrgQ = None, since: SinceQ = None,
                              min_count: Annotated[int, Query(ge=1, le=50)] = 2,
                              limit: Annotated[int, Query(ge=1, le=100)] = 20):
    org = await svc.resolve_org(session, org_id)
    return await svc.contradicted_claims(session, org, since, min_count, limit)


@router.get("/health", response_model=GraphHealthResponse)
async def graph_health_view(session: SessionDep, svc: Svc, org_id: OrgQ = None):
    """Projection lag, outbox backlog, Neo4j state, last projected event. Always 200 (it reports the degradation)."""
    org = str(org_id) if org_id else None
    return await svc.health(session, org)
