import uuid

from fastapi import APIRouter
from sqlalchemy import select

from app.api.deps import ActorDep, SessionDep
from app.api.errors import ApiError
from app.api.routes.organizations import get_org, normalize_domain, org_out
from app.api.routes.policy import build_policy_out, save_policy_settings
from app.core.audit import audit
from app.core.config import get_settings
from app.domain.enums import CapabilityState as CS
from app.models.core import Organization
from app.schemas.settings import IntegrationStatus, SettingsOut, SettingsUpdate
from app.services import capabilities as caps

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _fields(pairs: list[tuple[str, str]]) -> tuple[list[str], list[str]]:
    return [n for n, v in pairs if v], [n for n, v in pairs if not v]


async def integrations(session) -> list[IntegrationStatus]:
    """Connection state without secrets: only env var NAMES are exposed. Executors are reported separately and
    optional ones are never alarming: `manual` is always connected."""
    s = get_settings()
    profound = await caps.check_profound(session)
    llm = await caps.check_llm(False)
    out = []
    for cap, pairs in (
        (profound, [("PROFOUND_API_KEY", s.profound_api_key), ("PROFOUND_BASE_URL", s.profound_base_url)]),
        (llm, [("MODEL_API_KEY", s.model_api_key), ("MODEL_BASE_URL", s.model_base_url)]),
    ):
        have, missing = _fields(pairs)
        required_missing = [m for m in missing if m.endswith("KEY")]
        out.append(
            IntegrationStatus(
                key=cap.key, label=cap.label, connected=cap.state != CS.UNAVAILABLE and not required_missing,
                state=cap.state, configured_fields=have, missing_fields=missing,
                last_success=cap.last_success, last_error=cap.last_error, detail=cap.detail,
            )
        )  # fmt: skip
    for key, cap in (await caps.check_executors(False, session)).items():
        have, missing = _fields(
            [("GITHUB_TOKEN", s.github_token), ("GITHUB_OWNER", s.github_owner), ("GITHUB_REPO", s.github_repo)]
            if key == "github"
            else []
        )
        out.append(
            IntegrationStatus(
                key=key, label=cap.label, connected=cap.state == CS.HEALTHY, state=cap.state,
                configured_fields=have, missing_fields=missing if key == "github" else [],
                last_success=cap.last_success, last_error=cap.last_error, detail=cap.detail,
                optional=key != "manual", kind="executor",
            )
        )  # fmt: skip
    out.append(
        IntegrationStatus(
            key="cms", label="CMS executor (optional, not built)", connected=False, state=CS.UNAVAILABLE,
            detail="no CMS connector installed (optional)", optional=True, kind="executor",
        )
    )  # fmt: skip
    return out


@router.get("", response_model=SettingsOut)
async def get_settings_view(session: SessionDep, org_id: str | None = None):
    orgs = (await session.execute(select(Organization).order_by(Organization.created_at))).scalars().all()
    selected = None
    if org_id:
        selected = await get_org(session, uuid.UUID(org_id))
    elif orgs:
        selected = orgs[0]
    return SettingsOut(
        organization=await org_out(session, selected) if selected else None,
        organizations=[await org_out(session, o) for o in orgs],
        integrations=await integrations(session),
        policy=await build_policy_out(session),
        environment=get_settings().environment,
    )


@router.patch("", response_model=SettingsOut)
async def update_settings(body: SettingsUpdate, session: SessionDep, actor: ActorDep):
    if body.organization is not None:
        if not body.org_id:
            raise ApiError(
                "org_id is required to update organization settings",
                status_code=422,
                error_type="validation_error",
            )
        org = await get_org(session, uuid.UUID(body.org_id))
        for field, value in body.organization.model_dump(exclude_unset=True, exclude_none=True).items():
            if field.endswith("_domains"):
                try:
                    value = sorted({normalize_domain(v) for v in value})
                except ValueError as exc:
                    raise ApiError(str(exc), status_code=422, error_type="validation_error") from exc
            setattr(org, field, value)
        await audit(session, "human", actor, "organization", org.id, "organization.updated", {})
    if body.policy is not None:
        await save_policy_settings(session, body.policy)
        await audit(
            session,
            "human",
            actor,
            "policy",
            "settings",
            "policy.settings_updated",
            body.policy.model_dump(exclude_none=True),
        )
    await session.commit()
    return await get_settings_view(session, body.org_id)
