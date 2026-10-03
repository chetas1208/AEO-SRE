import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import Field

from app.domain.enums import IncidentState, Severity
from app.schemas.common import ApiModel, JobRef
from app.schemas.evidence import EvidenceItem

IncidentStatus = Literal[
    "detected", "investigating", "needs_review", "ready_for_action", "executing",
    "awaiting_measurement", "verified", "resolved", "dismissed", "failed",
]  # fmt: skip


class MetricDelta(ApiModel):
    label: str
    key: str | None = None
    before: float | None = None
    after: float | None = None
    value: float | None = None
    delta: float | None = None
    unit: str | None = None
    delta_pct: float | None = None
    favorable: bool | None = Field(None, description="True if the change is in the good direction")


class IncidentSummary(ApiModel):
    id: uuid.UUID
    number: int
    title: str
    severity: Severity
    state: IncidentState
    status: IncidentStatus
    display_state: str = Field(description="UI lifecycle label derived from state")
    category: str
    priority: float
    detected_at: datetime
    first_observed_at: datetime | None = None
    org_id: uuid.UUID
    topic: str | None = None
    context_label: str | None = None
    primary_delta: MetricDelta | None = None
    trend: list[float] = []
    investigation_status: str = "not_started"


class SeverityCounts(ApiModel):
    all: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0


class IncidentList(ApiModel):
    items: list[IncidentSummary]
    total: int
    limit: int
    offset: int
    counts_by_severity: dict[str, int]
    counts_by_status: dict[str, int]
    counts_by_state: dict[str, int] = {}
    topics: list[str] = []


class PriorityComponent(ApiModel):
    key: str
    label: str
    value: float | None = Field(None, description="0-100 display value")
    weight: float | None = None
    source: str | None = Field(None, description="measured|default; default means not measured")
    note: str | None = None


class PriorityBreakdown(ApiModel):
    score: float
    components: list[PriorityComponent] = []
    method: str | None = None
    raw: dict[str, Any] = {}
    note: str = "Intervention priority score (0-100); not a revenue estimate."


class CTA(ApiModel):
    key: str = Field(description="investigate|review|approve|execute|mark_executed|none|resolve")
    label: str
    enabled: bool
    reason: str | None = None
    intervention_id: uuid.UUID | None = None
    target_tab: str | None = None


class AllowedAction(ApiModel):
    action: str = Field(description="investigate|approve|reject|modify|execute|record_execution|verify|resolve|dismiss")
    enabled: bool
    reason: str | None = None


class ExpectedOutcome(ApiModel):
    available: bool
    n: int = 0
    reason: str | None = None
    low: float | None = None
    high: float | None = None
    unit: str | None = None
    metric: str | None = None


class IncidentExplanation(ApiModel):
    """Backend-owned reading of the incident. The frontend renders this and does not invent a cause."""

    what_changed: str
    why_it_matters: str
    leading_hypothesis: str | None = None
    hypothesis_status: str | None = None
    confidence: float | None = None
    supporting_evidence: list[str] = []
    counterevidence: list[str] = []
    recommended_action: str | None = None
    policy_scores: list[dict[str, Any]] = []
    expected_metric: str | None = None
    verification_window: str | None = None
    note: str = ""
    timings: dict[str, float | None] = {}
    independence_note: str | None = None


class IncidentDetail(IncidentSummary):
    summary: str = ""
    confidence: float | None = None
    metrics: list[MetricDelta] = []
    priority_breakdown: PriorityBreakdown
    primary_cta: CTA
    allowed_actions: list[AllowedAction] = []
    allowed_next_states: list[IncidentState] = []
    affected_prompt_count: int = 0
    active_job: JobRef | None = None
    expected_outcome: ExpectedOutcome
    experiment_id: uuid.UUID | None = None
    context: dict[str, Any] = {}
    explanation: IncidentExplanation


class PromptRow(ApiModel):
    prompt: str
    intent: str | None = None
    volume: float | None = None
    our_visibility: float | None = None
    competitor: str | None = None
    competitor_visibility: float | None = None
    engines: list[str] = []
    change: float | None = None
    unit: str | None = None
    persona: str | None = None
    topic: str | None = None
    meta: dict[str, Any] = {}


class PromptsOut(ApiModel):
    incident_id: uuid.UUID
    cluster_id: uuid.UUID | None = None
    topic: str | None = None
    items: list[PromptRow]
    total: int
    unavailable_reason: str | None = None


class IncidentEventOut(ApiModel):
    id: uuid.UUID
    seq: int
    incident_id: uuid.UUID
    timestamp: datetime
    stage: str
    event_type: str = ""
    status: str
    message: str
    metadata: dict[str, Any] = {}


class DetectRequest(ApiModel):
    org_id: uuid.UUID | None = None


class DetectOut(ApiModel):
    jobs: list[JobRef]


class InvestigateOut(ApiModel):
    incident_id: uuid.UUID
    job: JobRef
    state: IncidentState


class TransitionRequest(ApiModel):
    reason: str | None = None


class TransitionOut(ApiModel):
    incident_id: uuid.UUID
    from_state: IncidentState
    to_state: IncidentState


class EvidenceListOut(ApiModel):
    incident_id: uuid.UUID
    items: list[EvidenceItem]
    total: int
    counts_by_type: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    limit: int = 200
    offset: int = 0
