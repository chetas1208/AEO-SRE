from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import ActorDep, PageDep, SessionDep
from app.api.errors import ApiError
from app.core.audit import audit
from app.domain.enums import ActionType
from app.learning.review import acceptance_rate
from app.models.core import Setting
from app.models.policy import PolicyVersion
from app.schemas.policy import PolicyOut, PolicySettingsUpdate, PolicyVersionOut, PolicyVersionsOut
from app.services.capabilities import _artifact_version

router = APIRouter(prefix="/api/policy", tags=["policy"])
DEFAULT_MIN_CONFIDENCE = 0.5


async def load_policy_settings(session) -> dict:
    row = await session.get(Setting, "policy")
    value = dict(row.value or {}) if row else {}
    mask = value.get("allowed_actions") or {}
    return {
        "allowed_actions": {
            a.value: bool(mask.get(a.value, True)) or a == ActionType.OBSERVE for a in ActionType
        },
        "min_confidence": float(value.get("min_confidence", DEFAULT_MIN_CONFIDENCE)),
    }


async def save_policy_settings(session, update: PolicySettingsUpdate) -> None:
    row = await session.get(Setting, "policy")
    value = dict(row.value or {}) if row else {}
    if update.allowed_actions is not None:
        unknown = set(update.allowed_actions) - {a.value for a in ActionType}
        if unknown:
            raise ApiError(
                f"unknown actions: {sorted(unknown)}", status_code=422, error_type="validation_error"
            )
        mask = dict(value.get("allowed_actions") or {})
        mask.update({k: bool(v) for k, v in update.allowed_actions.items()})
        mask[ActionType.OBSERVE.value] = True  # observe is always allowed
        value["allowed_actions"] = mask
    if update.min_confidence is not None:
        value["min_confidence"] = update.min_confidence
    if row is None:
        session.add(Setting(key="policy", value=value))
    else:
        row.value = value


async def build_policy_out(session) -> PolicyOut:
    settings = await load_policy_settings(session)
    rate, decided = await acceptance_rate(session)
    latest = (
        await session.execute(
            select(PolicyVersion)
            .order_by(PolicyVersion.n_updates.desc(), PolicyVersion.created_at.desc())
            .limit(1)
        )
    ).scalar()
    if latest is None:
        return PolicyOut(
            available=True,
            unavailable_reason=None,
            current_version=None,
            learning_mode="cold_start",
            cold_start=True,
            allowed_actions=settings["allowed_actions"],
            min_confidence=settings["min_confidence"],
            model_artifact_version=_artifact_version(),
            recommendation_acceptance_rate=rate,
            recommendation_decisions=decided,
        )
    return PolicyOut(
        available=True,
        current_version=latest.version,
        algorithm=latest.algorithm,
        learning_mode="cold_start" if latest.n_updates == 0 else "learning",
        cold_start=latest.n_updates == 0,
        n_updates=latest.n_updates,
        allowed_actions=settings["allowed_actions"],
        min_confidence=settings["min_confidence"],
        last_update=latest.created_at,
        model_artifact_version=_artifact_version(),
        recommendation_acceptance_rate=rate,
        recommendation_decisions=decided,
    )


@router.get("", response_model=PolicyOut)
async def get_policy(session: SessionDep):
    return await build_policy_out(session)


@router.patch("", response_model=PolicyOut)
async def update_policy(body: PolicySettingsUpdate, session: SessionDep, actor: ActorDep):
    await save_policy_settings(session, body)
    await audit(
        session,
        "human",
        actor,
        "policy",
        "settings",
        "policy.settings_updated",
        body.model_dump(exclude_none=True),
    )
    await session.commit()
    return await build_policy_out(session)


@router.get("/versions", response_model=PolicyVersionsOut)
async def policy_versions(session: SessionDep, page: PageDep):
    total = (await session.execute(select(func.count()).select_from(PolicyVersion))).scalar_one()
    rows = (
        (
            await session.execute(
                select(PolicyVersion)
                .order_by(PolicyVersion.n_updates.desc(), PolicyVersion.created_at.desc())
                .limit(page.limit)
                .offset(page.offset)
            )
        )
        .scalars()
        .all()
    )
    items = [
        PolicyVersionOut(
            id=str(r.id), version=r.version, algorithm=r.algorithm, n_updates=r.n_updates,
            parent_id=str(r.parent_id) if r.parent_id else None, created_at=r.created_at,
            immutable=r.immutable,
        )
        for r in rows
    ]  # fmt: skip
    return PolicyVersionsOut(items=items, total=total)
