import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from app.schemas.changeguard import ChangeGuardVerdict
from app.schemas.common import ApiModel, JobRef


class InterventionOut(ApiModel):
    """UI.md §51 InterventionCandidate plus ids and approval/execution state."""

    id: uuid.UUID
    incident_id: uuid.UUID
    hypothesis_id: uuid.UUID | None = None
    action: str
    title: str
    score: float | None = None
    risk: str | None = None
    reason: str = ""
    selected: bool = False
    selection_basis: str | None = None
    policy_version: str | None = None
    cold_start: bool = False
    proposed_change: dict[str, Any] = {}
    execution_target: str | None = None
    executor: str | None = None
    rollback: str | None = None
    observation_window_hours: int | None = None
    approval_status: str = "pending"
    approval: dict[str, Any] | None = None
    execution: dict[str, Any] | None = None
    package: dict[str, Any] | None = Field(
        None, description="manual intervention package (exact change, steps, target, rollback, window)")
    manual_execution_pending: bool = Field(
        False, description="true while a human still has to apply the package and mark it executed")
    experiment_id: uuid.UUID | None = None
    based_on_experiments: int | None = None
    change_guard: ChangeGuardVerdict | None = Field(
        None, description="Change Guard verdict for this proposal; null = not checked yet (unavailable)")


class InterventionsOut(ApiModel):
    incident_id: uuid.UUID
    items: list[InterventionOut]
    selection_basis: str | None = None
    policy_version: str | None = None
    cold_start: bool = True
    unavailable_reason: str | None = None


class ApprovalRequest(ApiModel):
    note: str | None = None
    reason_code: str | None = Field(
        None,
        description="rejection category; not a reward. incorrect_root_cause, action_too_risky, "
        "action_too_costly, already_addressed, insufficient_evidence, not_strategically_important, other",
    )
    executor: str | None = Field(
        None, description="omit: manual (default, always available). 'github' only if configured (optional)")
    review_reason: str | None = Field(
        None, description="Change Guard: required to approve when the verdict is REQUIRE_REVIEW (why a human accepts it)")


class ModifyRequest(ApiModel):
    note: str | None = None
    modified_change: dict[str, Any] = Field(description="replacement proposed_change (files/diff/target)")
    executor: str | None = Field(None, description="omit: manual (default). 'github' only if configured (optional)")
    review_reason: str | None = Field(None, description="Change Guard: required when the verdict is REQUIRE_REVIEW")


class ExecuteRequest(ApiModel):
    executor: str | None = Field(None, description="omit: manual (default). 'github' only if configured (optional)")
    dry_run: bool | None = Field(None, description="GitHub executor only: preview without mutating (never learned from)")


class RecordExecutionRequest(ApiModel):
    executed_at: datetime | None = Field(None, description="when the change was applied; omit: now; never future")
    reference_url: str | None = Field(None, description="optional link to the live page / ticket / commit")
    note: str | None = None
    actual_change: str | None = Field(
        None, description="set ONLY if what you applied differs from the proposal; stored verbatim, flagged as deviation")


class ExecutedOut(ApiModel):
    intervention: InterventionOut
    incident_state: str
    experiment_id: uuid.UUID | None = None
    execution_id: uuid.UUID | None = None
    executed_at: datetime | None = None
    verification_window_start: datetime | None = None
    deviation: bool = False


class InterventionActionOut(ApiModel):
    intervention: InterventionOut
    incident_state: str
    experiment_id: uuid.UUID | None = None
    job: JobRef | None = None
    message: str | None = None
    at: datetime | None = None
