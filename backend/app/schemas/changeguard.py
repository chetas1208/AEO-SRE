"""Change Guard API models (snake_case)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, Field, field_validator, model_validator

from app.schemas.common import ApiModel

DecisionName = Literal["ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK"]
SourceMode = Literal["LIVE", "SIMULATED"]
SemanticState = Literal["ok", "degraded", "skipped_no_canonical_truth"]


def _short(max_len: int):
    def check(v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be empty")
        if len(v) > max_len:
            raise ValueError(f"at most {max_len} characters")
        return v

    return AfterValidator(check)


Claim = Annotated[str, _short(1000)]


class AgentRef(ApiModel):
    id: Annotated[str, _short(128)]
    name: Annotated[str, Field(max_length=255)] = ""


class ChangeSetIn(ApiModel):
    """A structured ChangeSet an agent intends to apply. `org_id` or `org_domain` (not both required)."""

    org_id: uuid.UUID | None = None
    org_domain: Annotated[str, Field(max_length=255)] | None = None
    agent: AgentRef
    profound_run_id: Annotated[str, Field(max_length=128)] | None = None
    source_mode: SourceMode = Field(
        description="LIVE = a real agent; SIMULATED = demo/test traffic, labelled everywhere in the UI")
    target_url: Annotated[str, Field(max_length=2048)] | None = Field(
        None, description="page the change touches; null for changes without a page target")
    action_type: Annotated[str, Field(max_length=48)] = Field(description="existing ActionType vocabulary")
    proposed_claims: list[Claim] = Field(default_factory=list, max_length=100)
    proposed_text: Annotated[str, Field(max_length=50_000)] | None = None
    proposed_diff: Annotated[str, Field(max_length=100_000)] | None = None
    reason: Annotated[str, Field(max_length=4000)] = ""
    expected_kpi: Annotated[str, Field(max_length=255)] | None = None
    risk: Literal["low", "medium", "high"] | None = None
    reversible: bool | None = None
    idempotency_key: Annotated[str, Field(min_length=1, max_length=255)] | None = Field(
        None, description="default = agent id + run id + proposal digest")
    prompt_cluster_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50,
                                                description="optional: prompt clusters the change addresses")
    prompts: list[Annotated[str, Field(max_length=500)]] = Field(default_factory=list, max_length=100,
                                                                 description="optional: prompts the change addresses")
    recheck: bool = Field(False, description="evaluate again (new decision) instead of replaying a stored one")

    @model_validator(mode="after")
    def _org(self) -> ChangeSetIn:
        if self.org_id is None and not (self.org_domain or "").strip():
            raise ValueError("org_id or org_domain is required")
        return self

    @field_validator("action_type")
    @classmethod
    def _action(cls, v: str) -> str:
        return v.strip()


class FindingOut(ApiModel):
    type: str
    check: int | None = Field(None, description="1 active experiment, 2 duplicate/conflict, 3 canonical truth")
    decision: DecisionName = Field(description="what this finding alone implies (ALLOW = informational)")
    severity: str
    reason: str
    references: dict[str, Any] = Field(default_factory=dict, description="experiment_code, change_set_id, canonical_claim_id ...")
    eligible_after: datetime | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ChangeCheckOut(ApiModel):
    id: uuid.UUID
    change_set_id: uuid.UUID
    org_id: uuid.UUID
    decision: DecisionName
    findings: list[FindingOut]
    eligible_after: datetime | None = Field(None, description="DELAY: when the change may be proposed again")
    merged_proposal: dict[str, Any] | None = Field(None, description="MERGE: suggested single change")
    semantic_check: SemanticState
    guard_version: str
    digest: str = Field(description="action digest: sha256 of the proposal + experiment context; approvals bind to it")
    proposal_digest: str
    replayed: bool = False
    agent: AgentRef
    source_mode: SourceMode
    origin: Literal["external", "intervention"] = "external"
    target_url: str | None = None
    target: str | None = Field(None, description="normalized target")
    action_type: str
    profound_run_id: str | None = None
    idempotency_key: str
    experiment_codes: list[str] = Field(default_factory=list)
    evaluated_at: datetime
    created_at: datetime


class ChangeCheckList(ApiModel):
    items: list[ChangeCheckOut]
    total: int
    limit: int
    offset: int


class ChangeGuardVerdict(ApiModel):
    """Guard verdict for AEO SRE's own proposed intervention (null = never checked: render 'unavailable')."""

    check_id: uuid.UUID
    decision: DecisionName
    findings: list[FindingOut]
    eligible_after: datetime | None = None
    merged_proposal: dict[str, Any] | None = None
    semantic_check: SemanticState
    guard_version: str
    digest: str
    evaluated_at: datetime
    stale: bool = Field(False, description="the proposal changed after this check; refresh before approving")
    blocks_approval: bool = Field(description="BLOCK/DELAY: Approve is refused (409 CHANGE_GUARD_BLOCKED)")
    requires_review_reason: bool = Field(description="REQUIRE_REVIEW: approve needs a typed review_reason")


class ProtectionCheck(ApiModel):
    id: uuid.UUID
    change_set_id: uuid.UUID
    decision: DecisionName
    agent_id: str
    agent_name: str = ""
    source_mode: SourceMode
    origin: str = "external"
    target: str | None = None
    action_type: str
    reasons: list[str] = Field(default_factory=list)
    eligible_after: datetime | None = None
    created_at: datetime


class Protection(ApiModel):
    protected: bool
    until: datetime | None = Field(None, description="eligible_after from the window service; null when not protected")
    until_basis: str | None = None
    targets: list[str] = Field(default_factory=list)
    checks_blocked_count: int = 0
    recent_checks: list[ProtectionCheck] = Field(default_factory=list)


class CanonicalClaimIn(ApiModel):
    key: Annotated[str, Field(min_length=1, max_length=128)]
    statement: Annotated[str, Field(min_length=3, max_length=2000)]
    entities: list[Annotated[str, Field(max_length=255)]] = Field(default_factory=list, max_length=50)
    scope: Annotated[str, Field(max_length=512)] | None = Field(
        None, description="'*'/empty = whole org; a URL limits the claim to changes in that section")
    valid_from: datetime | None = None
    valid_until: datetime | None = Field(None, description="claim expires after this instant (not enforced after)")
    source: Annotated[str, Field(max_length=512)] | None = Field(None, description="provenance")


class CanonicalClaimPatch(ApiModel):
    statement: Annotated[str, Field(min_length=3, max_length=2000)] | None = None
    entities: list[Annotated[str, Field(max_length=255)]] | None = Field(None, max_length=50)
    scope: Annotated[str, Field(max_length=512)] | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: Annotated[str, Field(max_length=512)] | None = None
    status: Literal["active", "retired"] | None = Field(None, description="'retired' soft-retires the claim")
    retire_reason: Annotated[str, Field(max_length=2000)] | None = None


class CanonicalClaimOut(ApiModel):
    id: uuid.UUID
    org_id: uuid.UUID
    key: str
    statement: str
    entities: list[str] = Field(default_factory=list)
    scope: str | None = None
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    source: str | None = None
    status: Literal["active", "retired"]
    created_by: str
    updated_by: str | None = None
    retired_at: datetime | None = None
    retired_by: str | None = None
    retire_reason: str | None = None
    created_at: datetime
    updated_at: datetime


class CanonicalClaimList(ApiModel):
    items: list[CanonicalClaimOut]
    total: int
    limit: int
    offset: int
