import json
import re
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.services.campaign_profound import live_profound_overlay, merge_profound_impact, resolve_brand_org_id
from app.services.live_surface import BUILT_IN_CAMPAIGN_IDS, iter_public_campaigns, live_surface_enabled

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

CAMPAIGNS_STORE_PATH = Path(__file__).resolve().parent.parent.parent / "data" / "campaigns_store.json"

class CampaignCreateRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=120)
    objective: str | None = Field(default="", max_length=500)
    budget: float = Field(default=10000.0, ge=0.0)
    channels: list[str] = Field(default_factory=lambda: ["Search LLMs", "Developer Docs"])
    primary_channel: str | None = None
    agents: list[str] = Field(default_factory=lambda: ["agt-citation-recovery"])
    primary_metric: str = Field(default="visibility")
    date_range: str | None = None
    owner: str = Field(default="Operations Lead")
    run_profound_agents: bool = Field(
        default=True,
        description="When true, enqueue background Profound agent runs after create (requires PROFOUND_API_KEY).",
    )


def patch_campaign_generation(campaign_id: str, generation: dict[str, Any]) -> None:
    """Update in-memory campaign store with Profound generation run results (worker callback)."""
    _sync_custom_from_disk()
    for c in CAMPAIGNS_DB:
        if c.get("id") != campaign_id:
            continue
        c["profound_generation"] = {**generation, "source": "PROFOUND", "source_mode": "LIVE"}
        runs = generation.get("runs") or []
        if runs:
            activity = []
            for r in runs:
                st = str(r.get("status") or "running").lower()
                activity.append(
                    {
                        "id": r.get("run_id") or f"run-{r.get('agent_id')}",
                        "name": r.get("agent_name") or r.get("agent_id"),
                        "profound_agent_id": r.get("agent_id"),
                        "profound_run_id": r.get("run_id"),
                        "role": "Profound Agent (LIVE)",
                        "source": "PROFOUND",
                        "source_mode": "LIVE",
                        "runs": 1,
                        "cost": 0.0,
                        "status": st.upper() if st in ("queued", "running", "succeeded", "failed") else "RUNNING",
                        "outputs": list((r.get("outputs") or {}).values()) if isinstance(r.get("outputs"), dict) else [],
                    }
                )
            c["agent_activity"] = activity
        tl = list(c.get("timeline") or [])
        tl.insert(
            0,
            {
                "time": "Just now",
                "event": f"Profound agent generation: {generation.get('status')}",
                "type": "agent",
            },
        )
        c["timeline"] = tl
        _save_custom_campaigns()
        return

CAMPAIGNS_DB: list[dict[str, Any]] = [

    {
        "id": "cmp-ai-discovery-launch-01",
        "name": "Enterprise AI Discovery Launch",
        "owner": "Sarah Jenkins (PMM Lead)",
        "status": "ACTIVE",
        "date_range": "Sep 15, 2026 – Oct 15, 2026",
        "channels": ["Search LLMs", "LinkedIn", "Developer Docs", "YouTube"],
        "primary_channel": "Search LLMs & Developer Docs",
        "currency": "USD",
        "budget": 50000.0,
        "total_cost": 42780.0,
        "attributed_return": 118000.0,
        "roi": 1.76,
        "measurement_confidence": "MEDIUM",
        "cost_completeness_pct": 92,
        "outcome_coverage_pct": 74,
        "return_sources": {
            "direct": 46000.0,
            "attributed": 51000.0,
            "modeled": 21000.0,
            "proxy": "+9.2pp Profound visibility"
        },
        "operational_metrics": {
            "gross_return": 118000.0,
            "net_return": 75220.0,
            "cost_per_output": 2037.14,
            "cost_per_agent_run": 57.57,
            "cost_per_approved_asset": 2415.0,
            "cost_per_lead": 2251.58,
            "cost_per_ai_visibility_point": 4650.0,
            "cost_per_citation_gain": 6684.38,
            "agent_roi": {
                "ratio": 3.82,
                "attribution_label": "ATTRIBUTED (outputs linked to deal touchpoints)"
            },
            "health_dimensions": {
                "cost_completeness": 92,
                "outcome_coverage": 74,
                "attribution_quality": "Medium",
                "ai_discovery_coverage": "High"
            }
        },
        "cost_composition": [
            {"category": "Paid Media", "amount": 18000.0, "pct": 42.1, "source": "OBSERVED"},
            {"category": "People", "amount": 11400.0, "pct": 26.6, "source": "ESTIMATED"},
            {"category": "Video Production", "amount": 6250.0, "pct": 14.6, "source": "ACTUAL"},
            {"category": "Creator Fees", "amount": 3500.0, "pct": 8.2, "source": "ACTUAL"},
            {"category": "Agent Runs", "amount": 2130.0, "pct": 5.0, "source": "OBSERVED"},
            {"category": "Tools & Other", "amount": 860.0, "pct": 2.0, "source": "OBSERVED"},
            {"category": "Model APIs", "amount": 640.0, "pct": 1.5, "source": "OBSERVED"}
        ],
        "cost_lineage": {
            "id": "root-cost",
            "name": "Total Campaign Cost",
            "amount": 42780.0,
            "children": [
                {
                    "id": "c-paid-media",
                    "name": "Paid Media",
                    "amount": 18000.0,
                    "children": [
                        {"id": "c-pm-linkedin", "name": "LinkedIn Sponsored Content", "amount": 11500.0},
                        {"id": "c-pm-search", "name": "Technical Search Placements", "amount": 6500.0}
                    ]
                },
                {
                    "id": "c-people",
                    "name": "People (Team & Contractors)",
                    "amount": 11400.0,
                    "children": [
                        {"id": "c-p-pmm", "name": "Product Marketing Lead (Sarah)", "amount": 4200.0, "details": "28 hrs @ $150/hr · MANUAL"},
                        {"id": "c-p-designer", "name": "Staff Brand Designer (Alex)", "amount": 3100.0, "details": "21.5 hrs @ $144/hr · MANUAL"},
                        {"id": "c-p-eng", "name": "Solutions Engineer (David)", "amount": 2600.0, "details": "16 hrs @ $162.50/hr · MANUAL"},
                        {"id": "c-p-creator-mgr", "name": "Creator Relations Manager (Elena)", "amount": 1500.0, "details": "15 hrs @ $100/hr · ESTIMATED"}
                    ]
                },
                {
                    "id": "c-video",
                    "name": "Video & Media",
                    "amount": 6250.0,
                    "children": [
                        {"id": "c-v-prod", "name": "Studio Production & Filming", "amount": 3400.0},
                        {"id": "c-v-edit", "name": "Post-Production Editing & Motion", "amount": 1800.0},
                        {"id": "c-v-ai", "name": "AI Voice & B-Roll Generation", "amount": 1050.0}
                    ]
                },
                {
                    "id": "c-creators",
                    "name": "Creator & Partner Fees",
                    "amount": 3500.0,
                    "children": [
                        {"id": "c-cr-deepdive", "name": "Tech Influencer Architectural Review", "amount": 3500.0}
                    ]
                },
                {
                    "id": "c-agents",
                    "name": "Profound & Custom Agent Runs",
                    "amount": 2130.0,
                    "children": [
                        {"id": "c-ag-citation", "name": "Profound Citation & Ingestion Agent", "amount": 1280.0, "details": "37 runs · 33 successful"},
                        {"id": "c-ag-copy", "name": "Content Structuring Agent", "amount": 850.0, "details": "18 runs · 15 successful"}
                    ]
                },
                {
                    "id": "c-model-apis",
                    "name": "Model APIs (Anthropic & OpenAI)",
                    "amount": 640.0,
                    "children": [
                        {"id": "c-api-haiku", "name": "Claude Haiku 4.5 Fast Inference", "amount": 210.0},
                        {"id": "c-api-sonnet", "name": "Claude Sonnet 4.6 Deep Reasoning", "amount": 430.0}
                    ]
                },
                {
                    "id": "c-tools",
                    "name": "Software Tools & Hosting",
                    "amount": 860.0
                }
            ]
        },
        "people": [
            {
                "id": "person-1",
                "name": "Sarah Jenkins",
                "role": "PMM Lead",
                "hours": 28.0,
                "hourly_cost": 150.0,
                "total_cost": 4200.0,
                "source_quality": "MANUAL",
                "outputs": ["Campaign Brief", "Landing Page Copy", "FAQ Schema"]
            },
            {
                "id": "person-2",
                "name": "Alex Rivera",
                "role": "Staff Designer",
                "hours": 21.5,
                "hourly_cost": 144.0,
                "total_cost": 3100.0,
                "source_quality": "MANUAL",
                "outputs": ["Interactive Architecture Diagram", "Video Graphics Deck"]
            },
            {
                "id": "person-3",
                "name": "David Chen",
                "role": "Solutions Engineer",
                "hours": 16.0,
                "hourly_cost": 162.5,
                "total_cost": 2600.0,
                "source_quality": "MANUAL",
                "outputs": ["Docker Gateway Sample", "Technical Integration Guide"]
            },
            {
                "id": "person-4",
                "name": "Elena Rostova",
                "role": "Creator Manager",
                "hours": 15.0,
                "hourly_cost": 100.0,
                "total_cost": 1500.0,
                "source_quality": "ESTIMATED",
                "outputs": ["Creator Brief", "Review Coordination"]
            }
        ],
        "agents": [
            {
                "id": "agent-profound-citation",
                "name": "Profound Citation Agent",
                "runs": 37,
                "successful_runs": 33,
                "failed_runs": 4,
                "tokens": 4200000,
                "model_cost": 189.0,
                "tool_cost": 312.0,
                "total_cost": 501.0,
                "outputs_produced": 12,
                "approved_outputs": 8,
                "approval_rate_pct": 66.7,
                "cost_per_approved_output": 62.6
            },
            {
                "id": "agent-content-audit",
                "name": "Canonical Truth Auditor Agent",
                "runs": 22,
                "successful_runs": 22,
                "failed_runs": 0,
                "tokens": 2850000,
                "model_cost": 124.0,
                "tool_cost": 225.0,
                "total_cost": 349.0,
                "outputs_produced": 9,
                "approved_outputs": 9,
                "approval_rate_pct": 100.0,
                "cost_per_approved_output": 38.8
            }
        ],
        "videos": [
            {
                "id": "vid-01",
                "title": "Enterprise SAML & Zero-Downtime Migration Walkthrough",
                "total_cost": 4920.0,
                "creator_cost": 2300.0,
                "editing_cost": 1200.0,
                "ai_generation_cost": 700.0,
                "distribution_cost": 720.0,
                "versions_count": 7,
                "published": True,
                "views": 48200,
                "engagement_pct": 5.4,
                "qualified_visits": 3140,
                "profound_citations_observed": 14,
                "muse_shortlist_events": 28,
                "leads_generated": 19
            }
        ],
        "assets": [
            {
                "id": "ast-landing-page",
                "title": "Verified Enterprise Migration Center",
                "type": "LandingPage",
                "created_by": "Sarah Jenkins",
                "generated_by_agent": "Canonical Truth Auditor Agent",
                "edited_by": "David Chen",
                "reviewed_by": "PMM Lead",
                "approved_by": "VP Marketing",
                "distributed_on": ["Web Canonical", "LinkedIn"],
                "downstream_outcomes": ["+9.2pp Profound visibility", "91 Muse shortlists", "19 Qualified leads"]
            },
            {
                "id": "ast-video-01",
                "title": "Video #1: Zero-Downtime Migration Benchmark",
                "type": "Video",
                "created_by": "Alex Rivera",
                "generated_by_agent": "Profound Content Agent",
                "edited_by": "Contract Editor",
                "reviewed_by": "Sarah Jenkins",
                "approved_by": "PMM Lead",
                "distributed_on": ["YouTube", "LinkedIn"],
                "downstream_outcomes": ["48.2K Views", "14 Citations"]
            }
        ],
        "waste_breakdown": {
            "potential_inefficiency": 12360.0,
            "items": [
                {"category": "REWORK", "label": "Discarded video revisions (v1-v4)", "amount": 4600.0},
                {"category": "DUPLICATE", "label": "Duplicate Agent web crawl runs", "amount": 2180.0},
                {"category": "ABANDONED", "label": "Unused comparison one-pagers", "amount": 3200.0},
                {"category": "REWORK", "label": "Repeated Model API queries from rate limits", "amount": 980.0},
                {"category": "REWORK", "label": "Excessive revision loops on schema copy", "amount": 1400.0}
            ]
        },
        "profound_impact": {
            "attribution_note": "Observed after campaign across 14 prompt clusters",
            "visibility_shift_pp": 9.2,
            "citation_share_shift_pp": 6.4,
            "prompt_coverage_pct": 78,
            "competitor_share_shift_pp": -8.1,
            "ai_perception_status": "Contradiction resolved in Claude & ChatGPT citations",
            "affected_clusters_count": 14
        },
        "muse_outcomes": {
            "matched_intents": 312,
            "shortlisted": 91,
            "details_requested": 37,
            "converted": 8,
            "funnel": [
                {"step": "Personal Agent Demand", "count": 312},
                {"step": "Profound Perception Aligned", "count": 218},
                {"step": "AgentMatch Surfaced", "count": 144},
                {"step": "Shortlisted by Buyer Agent", "count": 91},
                {"step": "Technical Details Requested", "count": 37},
                {"step": "Conversion / Closed Won", "count": 8}
            ]
        },
        "roi_confidence_breakdown": {
            "overall": "MEDIUM",
            "cost_completeness": 92,
            "revenue_coverage": 84,
            "people_cost_quality": "ESTIMATED",
            "profound_contribution": "Associated observation (+9.2pp visibility)",
            "muse_feedback": "Observed real agent interaction telemetry",
            "known_costs": ["Paid media", "Agents", "Videos", "Tools & Hosting"],
            "partial_costs": ["People internal hourly rates"],
            "missing_costs": ["Creator agency ancillary travel invoice #4"]
        },
        "timeline": [
            {"time": "Sep 15, 09:14", "event": "Campaign budget & brief initialized ($50K budget)", "type": "budget"},
            {"time": "Sep 16, 11:20", "event": "Agent run started: Canonical claim extraction", "type": "agent"},
            {"time": "Sep 18, 14:45", "event": "Landing Page Copy drafted & reviewed by PMM Lead", "type": "asset"},
            {"time": "Sep 22, 10:12", "event": "Video #1 rendered and approved by PMM Lead", "type": "video"},
            {"time": "Sep 23, 08:00", "event": "Campaign launched on LinkedIn and Search", "type": "launch"},
            {"time": "Sep 27, 16:30", "event": "Profound observed +9.2pp visibility shift", "type": "profound"},
            {"time": "Oct 01, 13:10", "event": "Muse recorded 91st shortlist event", "type": "muse"},
            {"time": "Oct 02, 18:40", "event": "First enterprise deal closed ($46,000 direct revenue)", "type": "revenue"}
        ]
    },
    {
        "id": "cmp-security-blitz-02",
        "name": "Q3 SAML & Security Verification Blitz",
        "owner": "David Chen (Solutions Eng)",
        "status": "COMPLETED",
        "date_range": "Aug 01, 2026 – Aug 31, 2026",
        "channels": ["Security Documentation", "Trust Portal", "Profound Ingestion"],
        "primary_channel": "Trust Portal & Documentation",
        "currency": "USD",
        "budget": 20000.0,
        "total_cost": 18400.0,
        "attributed_return": 54200.0,
        "roi": 1.95,
        "measurement_confidence": "HIGH",
        "cost_completeness_pct": 98,
        "outcome_coverage_pct": 88,
        "return_sources": {
            "direct": 32000.0,
            "attributed": 22200.0,
            "modeled": 0.0,
            "proxy": "+14.0pp Profound citation share"
        },
        "operational_metrics": {
            "gross_return": 54200.0,
            "net_return": 35800.0,
            "cost_per_output": 1226.67,
            "cost_per_agent_run": 121.43,
            "cost_per_approved_asset": 18400.0,
            "cost_per_lead": 3066.67,
            "cost_per_ai_visibility_point": 1483.87,
            "cost_per_citation_gain": 1314.29,
            "agent_roi": {
                "ratio": 4.65,
                "attribution_label": "DIRECT (schema adoption led to enterprise deal)"
            },
            "health_dimensions": {
                "cost_completeness": 98,
                "outcome_coverage": 88,
                "attribution_quality": "High",
                "ai_discovery_coverage": "High"
            }
        },
        "cost_composition": [
            {"category": "People", "amount": 9200.0, "pct": 50.0, "source": "ACTUAL"},
            {"category": "Agent Runs", "amount": 3400.0, "pct": 18.5, "source": "OBSERVED"},
            {"category": "Paid Media", "amount": 3000.0, "pct": 16.3, "source": "ACTUAL"},
            {"category": "Tools & Hosting", "amount": 1800.0, "pct": 9.8, "source": "ACTUAL"},
            {"category": "Model APIs", "amount": 1000.0, "pct": 5.4, "source": "OBSERVED"}
        ],
        "cost_lineage": {
            "id": "root-cost-2",
            "name": "Total Campaign Cost",
            "amount": 18400.0,
            "children": [
                {"id": "c2-people", "name": "Security & Eng Staff", "amount": 9200.0},
                {"id": "c2-agents", "name": "Documentation & Schema Agents", "amount": 3400.0},
                {"id": "c2-paid", "name": "Technical Syndicate Placements", "amount": 3000.0},
                {"id": "c2-tools", "name": "Security Portal Hosting", "amount": 1800.0},
                {"id": "c2-api", "name": "Model APIs", "amount": 1000.0}
            ]
        },
        "people": [
            {
                "id": "person-sec-1",
                "name": "David Chen",
                "role": "Solutions Engineer",
                "hours": 32.0,
                "hourly_cost": 162.5,
                "total_cost": 5200.0,
                "source_quality": "ACTUAL",
                "outputs": ["Security Whitepaper", "JSON-LD Trust Matrix"]
            },
            {
                "id": "person-sec-2",
                "name": "Marcus Vance",
                "role": "Security Architect",
                "hours": 20.0,
                "hourly_cost": 200.0,
                "total_cost": 4000.0,
                "source_quality": "ACTUAL",
                "outputs": ["SOC2 Evidence Pack", "SAML Spec Review"]
            }
        ],
        "agents": [
            {
                "id": "agent-sec-audit",
                "name": "Security Schema Synthesizer",
                "runs": 28,
                "successful_runs": 28,
                "failed_runs": 0,
                "tokens": 3100000,
                "model_cost": 160.0,
                "tool_cost": 240.0,
                "total_cost": 400.0,
                "outputs_produced": 14,
                "approved_outputs": 14,
                "approval_rate_pct": 100.0,
                "cost_per_approved_output": 28.5
            }
        ],
        "videos": [],
        "assets": [
            {
                "id": "ast-sec-portal",
                "title": "Canonical Security & Compliance Portal",
                "type": "TrustPortal",
                "created_by": "David Chen",
                "generated_by_agent": "Security Schema Synthesizer",
                "edited_by": "Marcus Vance",
                "reviewed_by": "Security Architect",
                "approved_by": "CISO",
                "distributed_on": ["trust.acme.example", "Profound Ingest"],
                "downstream_outcomes": ["+14.0pp Citation share", "$32K Direct deal signed"]
            }
        ],
        "waste_breakdown": {
            "potential_inefficiency": 1800.0,
            "items": [
                {"category": "REWORK", "label": "Schema re-indexing after domain redirect", "amount": 1800.0}
            ]
        },
        "profound_impact": {
            "attribution_note": "Verified experiment outcome across 8 security prompt clusters",
            "visibility_shift_pp": 12.4,
            "citation_share_shift_pp": 14.0,
            "prompt_coverage_pct": 92,
            "competitor_share_shift_pp": -11.2,
            "ai_perception_status": "All AI synthesizers now cite official SAML tier docs",
            "affected_clusters_count": 8
        },
        "muse_outcomes": {
            "matched_intents": 184,
            "shortlisted": 76,
            "details_requested": 42,
            "converted": 6,
            "funnel": [
                {"step": "Personal Agent Demand", "count": 184},
                {"step": "Security Constraints Checked", "count": 160},
                {"step": "Shortlisted by Buyer Agent", "count": 76},
                {"step": "Direct Conversion", "count": 6}
            ]
        },
        "roi_confidence_breakdown": {
            "overall": "HIGH",
            "cost_completeness": 98,
            "revenue_coverage": 88,
            "people_cost_quality": "ACTUAL",
            "profound_contribution": "Verified experiment (+14.0pp citation share)",
            "muse_feedback": "Observed verified agent shortlist events",
            "known_costs": ["All contractor and internal hours logged", "Direct cloud bills"],
            "partial_costs": [],
            "missing_costs": []
        },
        "timeline": [
            {"time": "Aug 01, 10:00", "event": "Campaign initialized with $20K budget", "type": "budget"},
            {"time": "Aug 10, 14:00", "event": "Security Whitepaper & Schema published", "type": "asset"},
            {"time": "Aug 18, 12:00", "event": "Profound verified +14pp citation share", "type": "profound"},
            {"time": "Aug 29, 16:30", "event": "$32,000 direct expansion closed", "type": "revenue"}
        ]
    }
]


def _load_custom_campaigns() -> None:
    if CAMPAIGNS_STORE_PATH.exists():
        try:
            with open(CAMPAIGNS_STORE_PATH, encoding="utf-8") as f:
                custom = json.load(f)
                existing_ids = {c["id"] for c in CAMPAIGNS_DB}
                for c in custom:
                    if c.get("id") not in existing_ids:
                        CAMPAIGNS_DB.append(c)
        except Exception:
            pass


def _sync_custom_from_disk() -> None:
    """Merge worker-written campaign updates (e.g. Profound generation) into the API process."""
    if not CAMPAIGNS_STORE_PATH.exists():
        return
    try:
        with open(CAMPAIGNS_STORE_PATH, encoding="utf-8") as f:
            custom = json.load(f)
        index = {c["id"]: i for i, c in enumerate(CAMPAIGNS_DB) if c.get("id")}
        for c in custom:
            cid = c.get("id")
            if not cid:
                continue
            if cid in index:
                CAMPAIGNS_DB[index[cid]] = c
            else:
                CAMPAIGNS_DB.append(c)
    except Exception:
        pass

def _save_custom_campaigns() -> None:
    try:
        CAMPAIGNS_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Save campaigns that were dynamically created
        custom = [c for c in CAMPAIGNS_DB if c["id"] not in BUILT_IN_CAMPAIGN_IDS]
        with open(CAMPAIGNS_STORE_PATH, "w", encoding="utf-8") as f:
            json.dump(custom, f, indent=2)
    except Exception:
        pass

# Initialize from disk
_load_custom_campaigns()


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_campaign(req: CampaignCreateRequest):
    """Create and persist a new marketing campaign initiative."""
    slug = re.sub(r"[^a-z0-9]+", "-", req.name.lower()).strip("-")[:24]
    cid = f"cmp-{slug}-{uuid.uuid4().hex[:6]}"
    primary_ch = req.primary_channel or (req.channels[0] if req.channels else "Search LLMs & Docs")

    new_campaign = {
        "id": cid,
        "name": req.name,
        "owner": req.owner or "Operations Lead",
        "objective": req.objective or f"Drive discoverability and growth for {req.name}",
        "status": "ACTIVE",
        "date_range": req.date_range or "Active Now",
        "channels": req.channels,
        "primary_channel": primary_ch,
        "currency": "USD",
        "budget": float(req.budget),
        "total_cost": 0.0,
        "attributed_return": 0.0,
        "roi": 0.0,
        "measurement_confidence": "HIGH",
        "cost_completeness_pct": 100,
        "outcome_coverage_pct": 100,
        "assigned_agents": req.agents,
        "primary_metric": req.primary_metric,
        "return_sources": {
            "direct": 0.0,
            "attributed": 0.0,
            "modeled": 0.0,
            "proxy": "+0.0pp Profound visibility"
        },
        "operational_metrics": {
            "gross_return": 0.0,
            "net_return": 0.0,
            "cost_per_output": 0.0,
            "cost_per_agent_run": 0.0,
            "cost_per_approved_asset": 0.0,
            "cost_per_lead": 0.0,
            "cost_per_ai_visibility_point": 0.0,
            "cost_per_citation_gain": 0.0,
            "agent_roi": {
                "ratio": 1.0,
                "attribution_label": "ATTRIBUTED (live campaign)"
            },
            "health_dimensions": {
                "cost_completeness": 100,
                "outcome_coverage": 100,
                "attribution_quality": "High",
                "ai_discovery_coverage": "High"
            }
        },
        "cost_composition": [
            {"category": "Agent Runs", "amount": 0.0, "pct": 0.0, "source": "OBSERVED"},
            {"category": "Model APIs", "amount": 0.0, "pct": 0.0, "source": "OBSERVED"},
        ],
        "cost_lineage": {
            "id": f"root-cost-{cid}",
            "name": "Total Campaign Cost",
            "amount": 0.0,
            "children": []
        },
        "people_breakdown": [],
        "agent_activity": [
            {
                "id": f"agt-run-{cid}",
                "name": a,
                "role": "Autonomous Operator",
                "runs": 0,
                "cost": 0.0,
                "status": "RUNNING",
                "outputs": []
            }
            for a in req.agents
        ],
        "timeline": [
            {"time": "Just now", "event": f"Campaign '{req.name}' created with ${req.budget:,.0f} budget", "type": "budget"}
        ],
        "profound_generation": {"status": "queued", "runs": [], "source": "PROFOUND", "source_mode": "LIVE"},
    }

    CAMPAIGNS_DB.insert(0, new_campaign)
    _save_custom_campaigns()

    if req.run_profound_agents:
        from app.services.profound_agents import enqueue_profound_generation

        job_id = await enqueue_profound_generation(
            scope="campaign",
            entity_id=cid,
            agent_ids=req.agents,
            context={
                "campaign_id": cid,
                "name": req.name,
                "objective": new_campaign.get("objective"),
                "primary_metric": req.primary_metric,
                "channels": req.channels,
            },
        )
        if job_id is not None:
            new_campaign["profound_generation"]["job_id"] = str(job_id)

    return new_campaign


@router.get("/profound/live")
async def campaigns_profound_live(session: SessionDep):
    """Live Profound metrics + 7d delta for the brand org (from ingested signals)."""
    return await live_profound_overlay(session)


@router.post("/profound/rerun-all")
async def campaigns_rerun_all_profound_agents():
    """Re-enqueue LIVE Profound agent generation for every custom campaign on disk."""
    from app.services.profound_agents import enqueue_profound_generation

    _sync_custom_from_disk()
    jobs: list[dict[str, str | None]] = []
    for c in iter_public_campaigns(CAMPAIGNS_DB):
        cid = str(c.get("id") or "")
        if not cid:
            continue
        job_id = await enqueue_profound_generation(
            scope="campaign",
            entity_id=cid,
            agent_ids=list(c.get("assigned_agents") or c.get("agents") or []),
            context={
                "campaign_id": cid,
                "name": c.get("name"),
                "objective": c.get("objective"),
                "primary_metric": c.get("primary_metric"),
                "channels": c.get("channels"),
            },
        )
        jobs.append({"campaign_id": cid, "job_id": str(job_id) if job_id else None})
    return {"status": "queued", "source_mode": "LIVE", "jobs": jobs}


@router.post("/profound/sync-runs")
async def campaigns_sync_all_agent_runs(session: SessionDep):
    """Poll Profound for latest LIVE run status on every campaign and experiment."""
    from app.services.profound_agents import sync_all_live_runs

    return await sync_all_live_runs(session)


@router.post("/{campaign_id}/profound/sync-runs")
async def campaign_sync_agent_runs(campaign_id: str):
    from app.services.profound_agents import sync_live_runs_for_campaign

    return await sync_live_runs_for_campaign(campaign_id)


@router.post("/{campaign_id}/profound/rerun")
async def campaign_rerun_profound_agents(campaign_id: str):
    """Re-enqueue LIVE Profound agent generation for a campaign."""
    _sync_custom_from_disk()
    camp = next((c for c in CAMPAIGNS_DB if c.get("id") == campaign_id), None)
    if camp is None:
        raise HTTPException(status_code=404, detail="Campaign not found")
    from app.services.profound_agents import enqueue_profound_generation

    job_id = await enqueue_profound_generation(
        scope="campaign",
        entity_id=campaign_id,
        agent_ids=list(camp.get("assigned_agents") or camp.get("agents") or []),
        context={
            "campaign_id": campaign_id,
            "name": camp.get("name"),
            "objective": camp.get("objective"),
            "primary_metric": camp.get("primary_metric"),
            "channels": camp.get("channels"),
        },
    )
    return {"status": "queued", "job_id": str(job_id) if job_id else None, "source_mode": "LIVE"}


@router.post("/profound/refresh")
async def campaigns_profound_refresh(session: SessionDep):
    """Pull latest Profound signals, then return the live overlay."""
    from app.services.pipeline import ingest

    oid = await resolve_brand_org_id(session)
    if oid is None:
        raise HTTPException(status_code=404, detail="No organization configured for Profound live overlay")
    ingest_result = await ingest(session, oid)
    overlay = await live_profound_overlay(session, org_id=oid)
    return {"ingest": ingest_result, "profound_live": overlay}


@router.get("")
async def list_campaigns(session: SessionDep):
    """List all tracked campaigns with financial summaries and live Profound effectiveness overlay."""
    _sync_custom_from_disk()
    overlay = await live_profound_overlay(session)
    enriched = [merge_profound_impact(dict(c), overlay) for c in iter_public_campaigns(CAMPAIGNS_DB)]
    return {
        "campaigns": enriched,
        "total": len(enriched),
        "profound_live": overlay,
        "source_mode": "LIVE" if live_surface_enabled() else "FIXTURE",
    }


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str, session: SessionDep):
    """Get single campaign detail."""
    _sync_custom_from_disk()
    overlay = await live_profound_overlay(session)
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return merge_profound_impact(dict(c), overlay)
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/costs")
async def get_campaign_costs(campaign_id: str):
    """Get detailed hierarchical cost lineage and cost composition."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {
                "total_cost": c["total_cost"],
                "cost_composition": c["cost_composition"],
                "cost_lineage": c["cost_lineage"],
                "cost_completeness_pct": c["cost_completeness_pct"]
            }
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/people")
async def get_campaign_people(campaign_id: str):
    """Get people contributors and human cost model."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {"people": c["people"]}
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/agents")
async def get_campaign_agents(campaign_id: str):
    """Get agent runs, model API costs, and output approval efficiencies."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {"agents": c["agents"]}
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/assets")
async def get_campaign_assets(campaign_id: str):
    """Get production assets and video drill-down."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {"assets": c["assets"], "videos": c["videos"]}
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/outcomes")
async def get_campaign_outcomes(campaign_id: str):
    """Get Profound perception signals, Muse interactions, and attributed revenue."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {
                "attributed_return": c["attributed_return"],
                "roi": c["roi"],
                "measurement_confidence": c["measurement_confidence"],
                "return_sources": c["return_sources"],
                "profound_impact": c["profound_impact"],
                "muse_outcomes": c["muse_outcomes"]
            }
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/timeline")
async def get_campaign_timeline(campaign_id: str):
    """Get chronological campaign event timeline."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return {"timeline": c["timeline"]}
    raise HTTPException(status_code=404, detail="Campaign not found")


@router.get("/{campaign_id}/graph")
async def get_campaign_graph(campaign_id: str):
    """Normalized 3D Campaign Graph with semantic depth z-levels:

    z = -12: Costs / Inputs (amber)
    z = -8: People / Agents (cyan / violet)
    z = -3: Assets / Production (cyan)
    z = 0: Campaign (white / indigo)
    z = +5: Distribution (blue)
    z = +9: Profound / Muse signals (purple / teal)
    z = +13: Business outcomes (green)
    """
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            nodes = [
                # z = 0: Campaign Node (Center)
                {
                    "id": c["id"],
                    "label": c["name"],
                    "type": "campaign",
                    "z": 0,
                    "color": "#6366f1",
                    "amount": c["total_cost"],
                    "status": c["status"]
                },
                # z = -12: Cost Inputs
                {
                    "id": f"cost-media-{c['id']}",
                    "label": "Paid Media ($18.0K)",
                    "type": "cost",
                    "z": -12,
                    "color": "#f59e0b",
                    "amount": 18000.0
                },
                {
                    "id": f"cost-people-{c['id']}",
                    "label": "People ($11.4K)",
                    "type": "cost",
                    "z": -12,
                    "color": "#f59e0b",
                    "amount": 11400.0
                },
                {
                    "id": f"cost-agents-{c['id']}",
                    "label": "Agents & APIs ($2.8K)",
                    "type": "cost",
                    "z": -12,
                    "color": "#f59e0b",
                    "amount": 2770.0
                },
                # z = -8: People & Agents
                {
                    "id": "node-person-sarah",
                    "label": "Sarah Jenkins (PMM Lead)",
                    "type": "person",
                    "z": -8,
                    "color": "#38bdf8",
                    "amount": 4200.0
                },
                {
                    "id": "node-person-alex",
                    "label": "Alex Rivera (Designer)",
                    "type": "person",
                    "z": -8,
                    "color": "#38bdf8",
                    "amount": 3100.0
                },
                {
                    "id": "node-agent-profound",
                    "label": "Profound Citation Agent",
                    "type": "agent",
                    "z": -8,
                    "color": "#818cf8",
                    "amount": 501.0
                },
                # z = -3: Assets
                {
                    "id": "node-asset-landing",
                    "label": "Verified Migration Landing Page",
                    "type": "asset",
                    "z": -3,
                    "color": "#22d3ee"
                },
                {
                    "id": "node-asset-video",
                    "label": "Zero-Downtime Video #1",
                    "type": "video",
                    "z": -3,
                    "color": "#22d3ee"
                },
                # z = +5: Distribution
                {
                    "id": "node-dist-linkedin",
                    "label": "LinkedIn Distribution",
                    "type": "channel",
                    "z": 5,
                    "color": "#60a5fa"
                },
                {
                    "id": "node-dist-web",
                    "label": "Canonical Web Index",
                    "type": "channel",
                    "z": 5,
                    "color": "#60a5fa"
                },
                # z = +9: Signals (Profound & Muse)
                {
                    "id": "node-sig-profound",
                    "label": "Profound: +9.2pp Visibility",
                    "type": "profound_signal",
                    "z": 9,
                    "color": "#a855f7"
                },
                {
                    "id": "node-sig-muse",
                    "label": "Muse: 91 Shortlists",
                    "type": "muse_interaction",
                    "z": 9,
                    "color": "#2dd4bf"
                },
                # z = +13: Business Outcomes
                {
                    "id": "node-outcome-leads",
                    "label": "19 Qualified Leads ($51K Pipeline)",
                    "type": "lead",
                    "z": 13,
                    "color": "#10b981",
                    "amount": 51000.0,
                    "confidence": "ATTRIBUTED"
                },
                {
                    "id": "node-outcome-revenue",
                    "label": "$46K Direct Won Revenue",
                    "type": "revenue",
                    "z": 13,
                    "color": "#10b981",
                    "amount": 46000.0,
                    "confidence": "DIRECT"
                }
            ]

            edges = [
                # Costs -> Campaign
                {"id": "e-cost-1", "source": f"cost-media-{c['id']}", "target": c["id"], "relation": "COST_OF", "color": "#f59e0b", "weight": 4},
                {"id": "e-cost-2", "source": f"cost-people-{c['id']}", "target": c["id"], "relation": "COST_OF", "color": "#f59e0b", "weight": 3},
                {"id": "e-cost-3", "source": f"cost-agents-{c['id']}", "target": c["id"], "relation": "COST_OF", "color": "#f59e0b", "weight": 2},
                # People/Agents -> Campaign
                {"id": "e-p1", "source": "node-person-sarah", "target": c["id"], "relation": "CONTRIBUTED_TO", "color": "#38bdf8"},
                {"id": "e-p2", "source": "node-person-alex", "target": c["id"], "relation": "CONTRIBUTED_TO", "color": "#38bdf8"},
                {"id": "e-ag1", "source": "node-agent-profound", "target": c["id"], "relation": "CONTRIBUTED_TO", "color": "#818cf8"},
                # People/Agents -> Assets
                {"id": "e-prod-1", "source": "node-person-sarah", "target": "node-asset-landing", "relation": "PRODUCED", "color": "#22d3ee"},
                {"id": "e-prod-2", "source": "node-person-alex", "target": "node-asset-video", "relation": "PRODUCED", "color": "#22d3ee"},
                {"id": "e-prod-3", "source": "node-agent-profound", "target": "node-asset-landing", "relation": "GENERATED", "color": "#818cf8"},
                # Assets -> Distribution
                {"id": "e-dist-1", "source": "node-asset-landing", "target": "node-dist-web", "relation": "DISTRIBUTED", "color": "#60a5fa"},
                {"id": "e-dist-2", "source": "node-asset-video", "target": "node-dist-linkedin", "relation": "DISTRIBUTED", "color": "#60a5fa"},
                # Distribution -> Signals
                {"id": "e-sig-1", "source": "node-dist-web", "target": "node-sig-profound", "relation": "AFFECTED", "color": "#a855f7"},
                {"id": "e-sig-2", "source": "node-dist-web", "target": "node-sig-muse", "relation": "AFFECTED", "color": "#2dd4bf"},
                # Signals -> Outcomes
                {"id": "e-out-1", "source": "node-sig-muse", "target": "node-outcome-leads", "relation": "RESULTED_IN", "color": "#10b981", "confidence": "ATTRIBUTED", "dashed": True},
                {"id": "e-out-2", "source": "node-outcome-leads", "target": "node-outcome-revenue", "relation": "RESULTED_IN", "color": "#10b981", "confidence": "DIRECT"}
            ]

            return {
                "nodes": nodes,
                "edges": edges,
                "summary": {
                    "total_cost": c["total_cost"],
                    "return": c["attributed_return"],
                    "roi": c["roi"],
                    "measurement_confidence": c["measurement_confidence"]
                }
            }

    raise HTTPException(status_code=404, detail="Campaign not found")
