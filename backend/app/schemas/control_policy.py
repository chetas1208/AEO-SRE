from datetime import datetime
from typing import Any

from app.schemas.common import ApiModel


class ControlPolicyStatusOut(ApiModel):
    mode: str
    shadow_enabled: bool
    laya_state: str
    laya_enabled: bool
    policy_algorithm: str
    policy_version: str
    laya_model: str | None = None
    meta: dict[str, Any] = {}


class ControlPolicyDecisionSummary(ApiModel):
    id: str
    change_check_id: str
    baseline_decision: str
    recommended_action: str | None
    agrees: bool | None
    laya_escalation_probability: float | None
    created_at: datetime
