"""Account signup/login and session cookies."""
from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import SessionDep
from app.api.deps_auth import OptionalUserDep, UserDep
from app.api.errors import ApiError, Conflict
from app.auth.service import AuthError, authenticate_user, signup, user_memberships
from app.auth.sessions import SESSION_COOKIE, create_session, revoke_session
from app.core.config import get_settings
from app.models.core import Organization

router = APIRouter(prefix="/api/auth", tags=["auth"])


class SignupIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=10, max_length=128)
    display_name: str = Field(default="", max_length=255)
    workspace_name: str = Field(default="", max_length=255)
    domain: str = Field(min_length=3, max_length=253)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str


class WorkspaceOut(BaseModel):
    organization_id: str
    name: str
    domain: str
    role: str


class MeOut(BaseModel):
    user_id: str
    email: str
    display_name: str
    workspaces: list[WorkspaceOut]


def _cookie_flags() -> dict:
    secure = get_settings().environment == "production"
    return {"httponly": True, "secure": secure, "samesite": "lax", "path": "/"}


async def _me(session: SessionDep, user) -> MeOut:
    memberships = await user_memberships(session, user.id)
    org_ids = [m.organization_id for m in memberships]
    orgs = {}
    if org_ids:
        for o in (await session.execute(select(Organization).where(Organization.id.in_(org_ids)))).scalars():
            orgs[o.id] = o
    return MeOut(
        user_id=str(user.id), email=user.email, display_name=user.display_name,
        workspaces=[
            WorkspaceOut(
                organization_id=str(m.organization_id),
                name=orgs[m.organization_id].name if m.organization_id in orgs else "",
                domain=orgs[m.organization_id].domain if m.organization_id in orgs else "",
                role=m.role,
            )
            for m in memberships
        ])


@router.post("/signup", response_model=MeOut, status_code=201)
async def auth_signup(body: SignupIn, response: Response, session: SessionDep) -> MeOut:
    try:
        user, org = await signup(
            session, email=str(body.email), password=body.password, display_name=body.display_name,
            workspace_name=body.workspace_name, domain=body.domain)
    except AuthError as exc:
        if exc.code in ("email_taken", "domain_taken"):
            raise Conflict(exc.message, {"code": exc.code}) from exc
        raise ApiError(exc.message, status_code=422, error_type=exc.code) from exc
    sess_row, _ = await create_session(session, user.id)
    await session.commit()
    response.set_cookie(SESSION_COOKIE, str(sess_row.id), **_cookie_flags())
    return MeOut(
        user_id=str(user.id), email=user.email, display_name=user.display_name,
        workspaces=[WorkspaceOut(organization_id=str(org.id), name=org.name, domain=org.domain, role="OWNER")])


@router.post("/login", response_model=MeOut)
async def auth_login(body: LoginIn, response: Response, session: SessionDep) -> MeOut:
    try:
        user = await authenticate_user(session, str(body.email), body.password)
    except AuthError as exc:
        raise ApiError(exc.message, status_code=401, error_type=exc.code) from exc
    sess_row, _ = await create_session(session, user.id)
    await session.commit()
    response.set_cookie(SESSION_COOKIE, str(sess_row.id), **_cookie_flags())
    return await _me(session, user)


@router.post("/logout", status_code=204)
async def auth_logout(
    response: Response,
    session: SessionDep,
    am_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    await revoke_session(session, am_session)
    await session.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeOut)
async def auth_me(session: SessionDep, user: UserDep) -> MeOut:
    return await _me(session, user)
