from __future__ import annotations

from typing import Any, Literal
from app.schemas.common import ApiModel


class ControlPlaneSummary(ApiModel):
    active_agents: int = 0
    running_campaigns: int = 0
    model_cost_today: float = 0.0
    attributed_return: float = 0.0
    decisions_needing_review: int = 0
    experiments_measuring: int = 0


class AgentActivity(ApiModel):
    id: str
    name: str
    role: str
    campaign_id: str
    campaign_name: str
    current_task: str
    runs: int = 0
    model_cost: float = 0.0
    total_cost: float = 0.0
    outputs_produced: int = 0
    outputs_accepted: int = 0
    attributed_outcome: Literal["POSITIVE", "NEGATIVE", "UNCERTAIN", "NOT_MEASURABLE"] = "NOT_MEASURABLE"
    state: Literal["RUNNING", "WAITING", "REVIEW", "BLOCKED", "COMPLETED", "FAILED"] = "RUNNING"
    last_active_at: str | None = None


class CampaignFinancialCard(ApiModel):
    id: str
    name: str
    status: str
    total_cost: float = 0.0
    attributed_return: float | None = None
    net_return: float | None = None
    roi_pct: float | None = None
    financial_status: Literal["POSITIVE", "NEGATIVE", "UNCERTAIN", "NOT_MEASURABLE"] = "NOT_MEASURABLE"
    measurement_confidence: Literal["HIGH", "MEDIUM", "LOW"] = "MEDIUM"
    return_source: Literal["DIRECT", "ATTRIBUTED", "MODELED", "PROXY", "UNKNOWN"] = "ATTRIBUTED"
    primary_channel: str = "Search LLMs & Docs"
    active_agents_count: int = 0
    active_experiments_count: int = 0


class DecisionCard(ApiModel):
    id: str
    title: str
    recommended_by: str
    campaign_id: str
    campaign_name: str
    action_type: str
    policy_version: str = "v0.3.1"
    status: Literal["PENDING_REVIEW", "APPROVED", "REJECTED", "MODIFIED"] = "PENDING_REVIEW"
    observed_outcome: Literal["POSITIVE", "NEGATIVE", "PENDING", "UNCERTAIN"] = "PENDING"
    context: str = ""
    cost: float = 0.0
    created_at: str
    decision_source: Literal["LAYA", "BANDIT", "RULE", "HUMAN"] = "RULE"
    laya_distribution: dict[str, float] | None = None
    laya_calibrated_confidence: float | None = None
    laya_model: str | None = None
    policy_mode: str = "SHADOW"
    risk_score: float | None = None


class ExperimentControlCard(ApiModel):
    id: str
    code: str
    name: str
    hypothesis: str
    action: str
    primary_metric: str
    status: str
    eligible_at: str | None = None
    target_key: str | None = None
    protection_active: bool = False


class ControlPlaneGraphNode(ApiModel):
    id: str
    type: str = "agent"  # agent, campaign, decision, experiment, outcome, signal, asset, person, cost, revenue, reward, laya_decision
    label: str
    status: str = "neutral"  # positive, negative, uncertain, neutral, running
    meta: dict[str, Any] = {}
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0


class ControlPlaneGraphEdge(ApiModel):
    id: str
    source: str
    target: str
    label: str = ""
    status: str = "neutral"  # positive, negative, uncertain, neutral, active
    confidence: str | None = None


class ControlPlaneGraph(ApiModel):
    nodes: list[ControlPlaneGraphNode] = []
    edges: list[ControlPlaneGraphEdge] = []


class ControlPlaneResponse(ApiModel):
    generated_at: str
    source_mode: Literal["LIVE", "TEST"] = "LIVE"
    summary: ControlPlaneSummary
    agents: list[AgentActivity]
    campaigns: list[CampaignFinancialCard]
    decisions: list[DecisionCard]
    experiments: list[ExperimentControlCard]
    graph: ControlPlaneGraph

