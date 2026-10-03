from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.api.deps import SessionDep
from app.core.audit import audit
from app.core.config import get_settings
from app.integrations.muse.ratelimit import LIMITER
from app.integrations.muse.tools import RecordFeedbackIn
from app.integrations.muse.v1.deps import muse_principal, require_scope
from app.integrations.muse.v1.errors import muse_error
from app.integrations.muse.v1.handlers import (
    FindMatchesIn,
    FindMatchesOut,
    campaign_summary,
    check_ai_perception,
    experiment_status,
    find_matches,
    record_feedback_v1,
)
from app.oauth.service import MusePrincipal

router = APIRouter(prefix="/api/muse/v1", tags=["muse-connector-v1"])


def _rate_limit(principal: MusePrincipal, write: bool) -> None:
    s = get_settings()
    key = f"muse:{principal.organization_id}:{principal.authorization_id}"
    LIMITER.hit(key, s.muse_write_rpm if write else s.muse_read_rpm)


@router.get("/me")
async def muse_me(principal: Annotated[MusePrincipal, Depends(muse_principal)]) -> dict:
    return {
        "organization_id": str(principal.organization_id),
        "user_id": str(principal.user_id),
        "scopes": list(principal.scopes),
        "client_id": principal.client_id,
    }


@router.post("/find-matches", response_model=FindMatchesOut)
async def v1_find_matches(
    body: FindMatchesIn,
    session: SessionDep,
    principal: Annotated[MusePrincipal, Depends(require_scope("matches:read"))],
):
    _rate_limit(principal, False)
    out = await find_matches(session, principal, body)
    await audit(session, "system", "muse-oauth", "muse_tool", principal.authorization_id, "muse.tool.called",
                {"tool": "find_matches", "org_id": str(principal.organization_id)})
    await session.commit()
    return out


class PerceptionIn(BaseModel):
    product_id: str = Field(min_length=1, max_length=128)


@router.post("/check-ai-perception")
async def v1_check_ai_perception(
    body: PerceptionIn,
    session: SessionDep,
    principal: Annotated[MusePrincipal, Depends(require_scope("discovery_gaps:read"))],
):
    _rate_limit(principal, False)
    product_id = body.product_id
    out = await check_ai_perception(session, principal, product_id)
    await session.commit()
    return out


class ExplainMatchIn(BaseModel):
    product_id: str
    intent: str = Field(default="")


@router.post("/explain-match")
async def v1_explain_match(body: ExplainMatchIn, session: SessionDep,
                           principal: Annotated[MusePrincipal, Depends(require_scope("matches:read"))]):
    _rate_limit(principal, False)
    fm = await find_matches(session, principal, FindMatchesIn(intent=body.intent or "match", constraints={}))
    return {
        "product_id": body.product_id,
        "constraints": [
            {"constraint": m, "status": "matched", "evidence": "canonical_truth", "confidence": "medium"}
            for m in (fm.matches[0].matched_constraints if fm.matches else [])
        ],
        "source": "muse_v1",
    }


@router.get("/campaigns/{campaign_id}/summary")
async def v1_campaign_summary(
    campaign_id: str,
    session: SessionDep,
    principal: Annotated[MusePrincipal, Depends(require_scope("campaigns:read"))],
):
    _rate_limit(principal, False)
    out = await campaign_summary(session, principal, campaign_id)
    if out.get("error") == "not_found":
        raise muse_error("not_found", "campaign not found in this workspace", 404)
    return out


@router.get("/experiments/{experiment_id}/status")
async def v1_experiment_status(
    experiment_id: str,
    session: SessionDep,
    principal: Annotated[MusePrincipal, Depends(require_scope("experiments:read"))],
):
    _rate_limit(principal, False)
    out = await experiment_status(session, principal, experiment_id)
    if out.get("error") == "not_found":
        raise muse_error("not_found", "experiment not found in this workspace", 404)
    return out


class FeedbackIn(BaseModel):
    target_type: str = Field(pattern="^(discovery_gap|change_check)$")
    target_id: str
    rating: str = Field(pattern="^(shortlisted|rejected|details_requested|accepted|helpful|not_helpful|incorrect|other)$")
    comment: str = Field(default="", max_length=1000)
    idempotency_key: str = Field(min_length=8, max_length=128)


@router.post("/feedback")
async def v1_feedback(
    body: FeedbackIn,
    session: SessionDep,
    principal: Annotated[MusePrincipal, Depends(require_scope("feedback:write"))],
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
):
    import uuid

    _rate_limit(principal, True)
    key = idempotency_key or body.idempotency_key
    mapped_rating = body.rating if body.rating in ("helpful", "not_helpful", "incorrect", "other") else "other"
    inp = RecordFeedbackIn(
        target_type=body.target_type,  # type: ignore[arg-type]
        target_id=uuid.UUID(body.target_id),
        rating=mapped_rating,  # type: ignore[arg-type]
        comment=body.comment,
        idempotency_key=key,
    )
    out = await record_feedback_v1(session, principal, inp)
    await audit(session, "system", "muse-oauth", "muse_feedback", principal.authorization_id, "muse.feedback.recorded",
                {"org_id": str(principal.organization_id), "rating": body.rating})
    await session.commit()
    return out.model_dump(mode="json")
