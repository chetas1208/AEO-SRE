import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.changeguard import Protection
from app.schemas.common import ApiModel, JobRef


class ExperimentRow(ApiModel):
    id: uuid.UUID
    number: int | None = None
    code: str
    incident_id: uuid.UUID | None = None
    incident_number: int | None = None
    incident_title: str | None = None
    action: str | None = None
    action_title: str | None = None
    started_at: datetime | None = None
    status: str
    display_status: str
    dry_run: bool = False
    executor: str | None = None
    awaiting_human_execution: bool = False
    deviation: bool = False
    measured: bool = False
    before: float | None = None
    after: float | None = None
    before_after_label: str | None = None
    reward: float | None = None
    policy_version: str | None = None
    outcome: str | None = None  # favorable|unfavorable|neutral|inconclusive; null = not assessed
    inconclusive_reason: str | None = None


class ExperimentSummary(ApiModel):
    running: int = 0
    awaiting_measurement: int = 0
    verified: int = 0
    total: int = 0


class ExperimentList(ApiModel):
    items: list[ExperimentRow]
    total: int
    limit: int
    offset: int
    summary: ExperimentSummary
    unavailable_reason: str | None = None


class TimelineEntry(ApiModel):
    at: datetime | None = None
    event: str
    actor: str | None = None
    detail: dict[str, Any] = {}


class ExperimentSpec(ApiModel):
    """The pre-declared hypothesis (Experiment.spec), frozen at activation."""

    if_action: str | None = None
    because_root_cause: str | None = None
    then_metric: str | None = None
    direction: str | None = None
    window_hours: float | None = None
    delay_hours: float | None = None
    statement: str | None = None
    observe: bool | None = None
    declared_at: datetime | None = None
    spec_hash: str | None = None


class DeclaredMetrics(ApiModel):
    primary: str | None = None
    secondary: list[str] = []


class OutcomeOut(ApiModel):
    """The persisted ExperimentOutcome row. Null on the detail = nothing assessed yet."""

    label: str  # favorable|unfavorable|neutral|inconclusive
    observe_outcome: str | None = None
    reward_total: float | None = None
    components: dict[str, float] = {}
    confounders: list[dict[str, Any]] = []
    causal_confidence: str | None = None
    causal_statement: str | None = None
    learning_applied: bool = False
    inconclusive_reason: str | None = None
    observed_at: datetime | None = None
    evaluated_at: datetime | None = None
    methodology: dict[str, Any] = {}


class OverrideOut(ApiModel):
    overridden: bool = False
    policy_action: str | None = None
    executed_action: str | None = None
    reason: str | None = None
    by: str | None = None


class VerificationInfo(ApiModel):
    executed_at: datetime | None = None
    eligible_at: datetime | None = None
    window_end: datetime | None = None
    delay_hours: float | None = None
    is_open: bool | None = None  # server clock: eligible_at <= now; null when there is no window yet
    rules: list[str] = []


class ExperimentDetail(ApiModel):
    """Sections follow UI.md §30."""

    id: uuid.UUID
    code: str
    summary: dict[str, Any]
    why_selected: dict[str, Any]
    context_at_decision: dict[str, Any]
    evidence_snapshot: Any
    action_executed: dict[str, Any]
    approval: dict[str, Any] | None
    before_metrics: Any
    after_metrics: Any
    reward: dict[str, Any] | None
    policy: dict[str, Any] | None
    timeline: list[TimelineEntry]
    awaiting_reward: bool
    display_status: str
    spec: ExperimentSpec | None = None
    declared_metrics: DeclaredMetrics | None = None
    outcome: OutcomeOut | None = None
    override: OverrideOut | None = None
    verification: VerificationInfo | None = None
    protection: Protection | None = Field(
        None, description="Change Guard: is this experiment protected from new changes, until when")


class VerifyOut(ApiModel):
    experiment_id: uuid.UUID
    job: JobRef


class ExperimentCreateIn(ApiModel):
    name: str = Field(min_length=3, max_length=256, description="Human-readable title for the experiment")
    hypothesis: str = Field(min_length=10, max_length=2000, description="Mandatory causal hypothesis declaring expected change")
    selected_action: str = Field(
        default="update_existing_page",
        description="ActionType e.g. update_existing_page, create_canonical_page, create_faq, structured_data, publisher_outreach, observe"
    )
    target_url: str | None = Field(default=None, max_length=1024, description="Target URL being modified or tested")
    target_key: str | None = Field(default=None, max_length=512, description="Target identifier or claim key")
    primary_metric: str = Field(
        default="visibility",
        description="Primary metric to verify: visibility, citation_share, accuracy, or competitor_share"
    )
    secondary_metrics: list[str] = Field(default_factory=list)
    verification_window_hours: float = Field(default=48.0, ge=1.0, le=720.0, description="Hours to wait before verification eligibility")
    incident_id: uuid.UUID | None = None
    campaign_id: str | None = None
    org_id: uuid.UUID | None = None
    notes: str | None = None
    dry_run: bool = False
    auto_activate: bool = True
    run_mode: str = Field(
        default="live",
        description="Internal automation only: live (Profound baseline + worker verify) or test (CI sandbox).",
    )
