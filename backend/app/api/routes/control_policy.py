"""Control-plane decision engine status (Laya + GraphLinUCB shadow)."""
from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import SessionDep
from app.control_policy.learner import ALGORITHM, POLICY_VERSION
from app.core.config import get_settings
from app.intelligence.laya.health import laya_capability_block
from app.models.control_policy import ControlPolicyDecision
from app.schemas.control_policy import ControlPolicyDecisionSummary, ControlPolicyStatusOut

router = APIRouter(prefix="/api/control-policy", tags=["control-policy"])


@router.get("/status", response_model=ControlPolicyStatusOut)
async def control_policy_status() -> ControlPolicyStatusOut:
    s = get_settings()
    laya = laya_capability_block()
    return ControlPolicyStatusOut(
        mode=(s.control_policy_mode or "SHADOW").upper(),
        shadow_enabled=s.control_policy_shadow_enabled,
        laya_state=str(laya.get("state", "UNAVAILABLE")),
        laya_enabled=s.laya_enabled,
        policy_algorithm=ALGORITHM,
        policy_version=POLICY_VERSION,
        laya_model=laya.get("model_version"),
        meta={"laya": laya},
    )


@router.get("/decisions/recent", response_model=list[ControlPolicyDecisionSummary])
async def recent_shadow_decisions(
    session: SessionDep,
    limit: int = 20,
) -> list[ControlPolicyDecisionSummary]:
    limit = max(1, min(limit, 100))
    rows = (
        await session.execute(
            select(ControlPolicyDecision)
            .where(ControlPolicyDecision.mode == "SHADOW")
            .order_by(ControlPolicyDecision.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [
        ControlPolicyDecisionSummary(
            id=str(r.id),
            change_check_id=str(r.change_check_id),
            baseline_decision=r.baseline_decision,
            recommended_action=r.recommended_action,
            agrees=r.agrees,
            laya_escalation_probability=r.laya_escalation_probability,
            created_at=r.created_at,
        )
        for r in rows
    ]
