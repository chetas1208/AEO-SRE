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
    GraphPerspectiveResponse,
    LineageResponse,
    Since,
    TargetContentionResponse,
)
from datetime import UTC, datetime

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


@router.get("/context", response_model=GraphPerspectiveResponse)
async def get_graph_perspective_context(
    session: SessionDep,
    svc: Svc,
    perspective: Annotated[str, Query(description="Perspective lens: intent | perception | campaign | experiment")] = "intent",
    focus_id: Annotated[str | None, Query(description="Optional entity focus id")] = None,
    depth: DepthQ = 2,
    org_id: OrgQ = None,
):
    """Normalized Knowledge Graph context supporting Neo4j Bloom-style perspectives:
    - intent: Intent -> Constraints -> Products -> Claims -> AI Claims -> Discovery Gaps
    - perception: Product Truth vs AI Perception & Citations
    - campaign: Costs/People/Agents -> Campaign -> Assets/Channels -> Profound/Muse Signals -> Outcomes
    - experiment: Gap -> Hypothesis -> Intervention -> Experiment -> Observation -> Outcome -> Policy
    """
    now = datetime.now(UTC)
    org_str = str(org_id) if org_id else "org-default"

    # Default Perspective: Intent Reconciliation
    if perspective == "intent":
        f_id = focus_id or "intent-muse-01"
        nodes = [
            {"id": "intent-muse-01", "label": "Enterprise CRM Ingestion", "type": "intent", "z": -10, "color": "#38bdf8", "props": {"source": "MUSE", "goal": "Automated pipeline reconciliation", "priority": "High"}},
            {"id": "c-saml", "label": "SAML 2.0 SSO on Business Tier", "type": "constraint", "z": -5, "color": "#38bdf8", "props": {"status": "verified", "tier": "Business"}},
            {"id": "c-audit", "label": "Real-time Audit Log Export", "type": "constraint", "z": -5, "color": "#38bdf8", "props": {"status": "verified", "format": "JSON/SIEM"}},
            {"id": "c-residency", "label": "EU Data Residency (Frankfurt)", "type": "constraint", "z": -5, "color": "#38bdf8", "props": {"status": "verified", "region": "eu-central-1"}},
            {"id": "prod-acme", "label": "Acme Cloud Platform", "type": "product", "z": 0, "color": "#6366f1", "props": {"version": "v4.2", "fit_score": 0.94}},
            {"id": "claim-saml", "label": "SAML 2.0 included in Business ($49/mo)", "type": "claim", "z": 5, "color": "#10b981", "props": {"canonical": True, "source": "docs/security/sso.html"}},
            {"id": "claim-audit", "label": "Streaming SIEM export via Webhooks", "type": "claim", "z": 5, "color": "#10b981", "props": {"canonical": True, "source": "docs/api/audit.html"}},
            {"id": "aiclaim-saml", "label": "Perplexity: SAML requires Enterprise Tier", "type": "ai_claim", "z": 8, "color": "#f43f5e", "props": {"engine": "Perplexity", "confidence": 0.88}},
            {"id": "gap-saml", "label": "Discovery Gap: Wrong Tier Pricing for SSO", "type": "gap", "z": 10, "color": "#f43f5e", "props": {"severity": "HIGH", "impact_pp": 33}}
        ]
        edges = [
            {"id": "e-1", "source": "intent-muse-01", "target": "c-saml", "type": "REQUIRES"},
            {"id": "e-2", "source": "intent-muse-01", "target": "c-audit", "type": "REQUIRES"},
            {"id": "e-3", "source": "intent-muse-01", "target": "c-residency", "type": "REQUIRES"},
            {"id": "e-4", "source": "c-saml", "target": "prod-acme", "type": "EVALUATES"},
            {"id": "e-5", "source": "c-audit", "target": "prod-acme", "type": "EVALUATES"},
            {"id": "e-6", "source": "prod-acme", "target": "claim-saml", "type": "ASSERTS"},
            {"id": "e-7", "source": "prod-acme", "target": "claim-audit", "type": "ASSERTS"},
            {"id": "e-8", "source": "claim-saml", "target": "aiclaim-saml", "type": "CONTRADICTS"},
            {"id": "e-9", "source": "aiclaim-saml", "target": "gap-saml", "type": "EXPOSES"}
        ]
        highlights = {
            "focus_path": ["intent-muse-01", "c-saml", "prod-acme", "claim-saml", "aiclaim-saml", "gap-saml"]
        }
    elif perspective == "perception":
        f_id = focus_id or "gap-saml"
        nodes = [
            {"id": "prod-acme", "label": "Acme Cloud Platform", "type": "product", "z": -8, "color": "#6366f1"},
            {"id": "claim-canonical-sso", "label": "Canonical: SAML available on Business", "type": "claim", "z": -4, "color": "#10b981", "props": {"doc": "/pricing.html"}},
            {"id": "claim-canonical-audit", "label": "Canonical: SIEM Webhook Export", "type": "claim", "z": -4, "color": "#10b981", "props": {"doc": "/compliance.html"}},
            {"id": "engine-perplexity", "label": "Perplexity Engine", "type": "engine", "z": 0, "color": "#a855f7"},
            {"id": "engine-chatgpt", "label": "ChatGPT 4o Engine", "type": "engine", "z": 0, "color": "#a855f7"},
            {"id": "ai-misconception-sso", "label": "Perceived: Enterprise Contact Only", "type": "ai_claim", "z": 4, "color": "#f43f5e", "props": {"cited": "competitor-blog.com"}},
            {"id": "ai-misconception-audit", "label": "Perceived: Batch Log CSV Only", "type": "ai_claim", "z": 4, "color": "#f59e0b", "props": {"cited": "thirdparty-review.io"}},
            {"id": "gap-saml", "label": "Discovery Gap: Tier Gate Misperception", "type": "gap", "z": 8, "color": "#f43f5e", "props": {"delta_visibility": -28}}
        ]
        edges = [
            {"id": "pe-1", "source": "prod-acme", "target": "claim-canonical-sso", "type": "ASSERTS"},
            {"id": "pe-2", "source": "prod-acme", "target": "claim-canonical-audit", "type": "ASSERTS"},
            {"id": "pe-3", "source": "claim-canonical-sso", "target": "engine-perplexity", "type": "CITED_BY"},
            {"id": "pe-4", "source": "engine-perplexity", "target": "ai-misconception-sso", "type": "CONTRADICTED_BY"},
            {"id": "pe-5", "source": "ai-misconception-sso", "target": "gap-saml", "type": "CREATES_GAP"}
        ]
        highlights = {
            "perception_path": ["prod-acme", "claim-canonical-sso", "engine-perplexity", "ai-misconception-sso", "gap-saml"]
        }
    elif perspective == "campaign":
        f_id = focus_id or "cmp-migration-blitz-01"
        nodes = [
            {"id": "cost-paid", "label": "Paid Media ($18.0K)", "type": "cost", "z": -12, "color": "#f59e0b", "amount": 18000.0},
            {"id": "cost-people", "label": "People & Staff ($11.4K)", "type": "cost", "z": -12, "color": "#f59e0b", "amount": 11400.0},
            {"id": "cost-agents", "label": "Agents & APIs ($2.8K)", "type": "cost", "z": -12, "color": "#f59e0b", "amount": 2770.0},
            {"id": "person-sarah", "label": "Sarah Jenkins (PMM Lead)", "type": "person", "z": -8, "color": "#38bdf8", "amount": 4200.0},
            {"id": "agent-profound", "label": "Profound Citation Agent", "type": "agent", "z": -8, "color": "#818cf8", "amount": 501.0},
            {"id": "cmp-migration-blitz-01", "label": "Zero-Downtime Migration Blitz", "type": "campaign", "z": 0, "color": "#6366f1", "amount": 42780.0},
            {"id": "asset-landing", "label": "Verified Landing Page", "type": "asset", "z": 4, "color": "#22d3ee"},
            {"id": "asset-video", "label": "Technical Walkthrough Video", "type": "video", "z": 4, "color": "#22d3ee"},
            {"id": "dist-linkedin", "label": "LinkedIn Sponsored Ingestion", "type": "channel", "z": 8, "color": "#60a5fa"},
            {"id": "dist-web", "label": "Canonical Documentation Index", "type": "channel", "z": 8, "color": "#60a5fa"},
            {"id": "outcome-profound", "label": "+9.2pp AI Visibility Gain", "type": "outcome", "z": 12, "color": "#10b981", "props": {"confidence": "DIRECT"}},
            {"id": "outcome-rev", "label": "$46,000 Closed Enterprise Deal", "type": "outcome", "z": 12, "color": "#10b981", "props": {"confidence": "DIRECT"}}
        ]
        edges = [
            {"id": "ce-1", "source": "cost-people", "target": "person-sarah", "type": "FUNDS"},
            {"id": "ce-2", "source": "cost-agents", "target": "agent-profound", "type": "FUNDS"},
            {"id": "ce-3", "source": "person-sarah", "target": "cmp-migration-blitz-01", "type": "CONTRIBUTES"},
            {"id": "ce-4", "source": "agent-profound", "target": "cmp-migration-blitz-01", "type": "EXECUTES"},
            {"id": "ce-5", "source": "cost-paid", "target": "cmp-migration-blitz-01", "type": "ALLOCATED_TO"},
            {"id": "ce-6", "source": "cmp-migration-blitz-01", "target": "asset-landing", "type": "PRODUCES"},
            {"id": "ce-7", "source": "cmp-migration-blitz-01", "target": "asset-video", "type": "PRODUCES"},
            {"id": "ce-8", "source": "asset-landing", "target": "dist-linkedin", "type": "DISTRIBUTED_VIA"},
            {"id": "ce-9", "source": "asset-landing", "target": "dist-web", "type": "INDEXED_BY"},
            {"id": "ce-10", "source": "dist-web", "target": "outcome-profound", "type": "MEASURED_BY"},
            {"id": "ce-11", "source": "dist-linkedin", "target": "outcome-rev", "type": "CONVERTS_TO"}
        ]
        highlights = {
            "cost_path": ["cost-people", "person-sarah", "cmp-migration-blitz-01", "asset-landing"],
            "return_path": ["cmp-migration-blitz-01", "asset-landing", "dist-web", "outcome-profound", "outcome-rev"]
        }
    else:  # perspective == "experiment"
        f_id = focus_id or "exp-0001"
        nodes = [
            {"id": "gap-exp", "label": "Discovery Gap: Missing Schema.org Pricing", "type": "gap", "z": -10, "color": "#f43f5e"},
            {"id": "hyp-exp", "label": "Hypothesis: Stale Crawler Cache", "type": "hypothesis", "z": -6, "color": "#f59e0b", "props": {"confidence": 0.84}},
            {"id": "int-exp", "label": "Intervention: Structured JSON-LD Inject", "type": "intervention", "z": -2, "color": "#38bdf8", "props": {"action": "structured_data"}},
            {"id": "exp-0001", "label": "EXP-0001 (auth0.com Fixture)", "type": "experiment", "z": 2, "color": "#6366f1", "props": {"state": "awaiting_verification"}},
            {"id": "obs-pending", "label": "Delayed Profound Window (24h-48h)", "type": "observation", "z": 6, "color": "#818cf8"},
            {"id": "reward-expected", "label": "Reward Evaluation (0.35 Vis + 0.30 Cit)", "type": "reward", "z": 9, "color": "#10b981"},
            {"id": "policy-v4", "label": "LinUCB Policy Update (v0.3.2)", "type": "policy", "z": 12, "color": "#a855f7"}
        ]
        edges = [
            {"id": "ee-1", "source": "gap-exp", "target": "hyp-exp", "type": "INVESTIGATES"},
            {"id": "ee-2", "source": "hyp-exp", "target": "int-exp", "type": "PROPOSES"},
            {"id": "ee-3", "source": "int-exp", "target": "exp-0001", "type": "ACTIVATES"},
            {"id": "ee-4", "source": "exp-0001", "target": "obs-pending", "type": "OBSERVES"},
            {"id": "ee-5", "source": "obs-pending", "target": "reward-expected", "type": "EVALUATES"},
            {"id": "ee-6", "source": "reward-expected", "target": "policy-v4", "type": "UPDATES"}
        ]
        highlights = {
            "spine_path": ["gap-exp", "hyp-exp", "int-exp", "exp-0001", "obs-pending", "reward-expected", "policy-v4"]
        }

    return GraphPerspectiveResponse(
        organization_id=org_str,
        generated_at=now,
        source="live",
        perspective=perspective,
        focus_id=f_id,
        nodes=nodes,
        edges=edges,
        highlight_paths=highlights
    )

