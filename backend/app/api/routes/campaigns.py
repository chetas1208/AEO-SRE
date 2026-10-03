"""CampaignGraph ROI API Routes: Investment -> People & Agents -> Assets -> Distribution -> Profound & Muse -> Outcomes."""

import uuid
from typing import Any
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/campaigns", tags=["campaigns"])

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


@router.get("")
async def list_campaigns():
    """List all tracked campaigns with financial summaries and confidence scores."""
    return {"campaigns": CAMPAIGNS_DB, "total": len(CAMPAIGNS_DB)}


@router.get("/{campaign_id}")
async def get_campaign(campaign_id: str):
    """Get single campaign detail."""
    for c in CAMPAIGNS_DB:
        if c["id"] == campaign_id:
            return c
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
