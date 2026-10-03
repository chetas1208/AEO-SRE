from datetime import datetime

from pydantic import Field

from app.schemas.common import ApiModel


class PolicyVersionOut(ApiModel):
    id: str | None = None
    version: str
    algorithm: str | None = None
    n_updates: int = 0
    parent_id: str | None = None
    created_at: datetime | None = None
    immutable: bool = True


class PolicyOut(ApiModel):
    available: bool
    unavailable_reason: str | None = None
    current_version: str | None = None
    algorithm: str | None = None
    learning_mode: str = Field(description="cold_start|learning|unavailable")
    cold_start: bool = True
    n_updates: int = 0
    allowed_actions: dict[str, bool] = {}
    human_approval_required: bool = True
    min_confidence: float = 0.0
    last_update: datetime | None = None
    model_artifact_version: str | None = None
    recommendation_acceptance_rate: float | None = None
    recommendation_decisions: int = 0
    acceptance_note: str = (
        "Acceptance is a quality signal. It is not a learning reward and is not optimized."
    )


class PolicyVersionsOut(ApiModel):
    items: list[PolicyVersionOut]
    total: int
    unavailable_reason: str | None = None


class PolicySettingsUpdate(ApiModel):
    allowed_actions: dict[str, bool] | None = None
    min_confidence: float | None = Field(None, ge=0, le=1)
