from __future__ import annotations

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routes.organizations import default_name, normalize_domain
from app.auth.password import hash_password, verify_password
from app.models.auth import OrganizationMembership, User
from app.models.core import Organization

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    def __init__(self, message: str, code: str = "auth_error") -> None:
        self.message = message
        self.code = code
        super().__init__(message)


async def signup(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    display_name: str,
    workspace_name: str,
    domain: str,
) -> tuple[User, Organization]:
    em = email.strip().lower()
    if not _EMAIL.match(em):
        raise AuthError("invalid email", "invalid_email")
    if len(password) < 10:
        raise AuthError("password must be at least 10 characters", "weak_password")
    existing = (await session.execute(select(User).where(User.email == em))).scalar()
    if existing is not None:
        raise AuthError("email already registered", "email_taken")
    try:
        host = normalize_domain(domain)
    except ValueError as exc:
        raise AuthError(str(exc), "invalid_domain") from exc
    org_taken = (await session.execute(select(Organization).where(Organization.domain == host))).scalar()
    if org_taken is not None:
        raise AuthError("workspace domain already exists", "domain_taken")
    user = User(email=em, display_name=(display_name or em.split("@")[0])[:255], password_hash=hash_password(password))
    session.add(user)
    await session.flush()
    slug = re.sub(r"[^a-z0-9]+", "-", host.split(".")[0].lower()).strip("-")[:48] or "workspace"
    org = Organization(name=(workspace_name or default_name(host)).strip()[:255], domain=host, slug=slug)
    session.add(org)
    await session.flush()
    session.add(OrganizationMembership(user_id=user.id, organization_id=org.id, role="OWNER"))
    await session.flush()
    return user, org


async def authenticate_user(session: AsyncSession, email: str, password: str) -> User:
    em = email.strip().lower()
    user = (await session.execute(select(User).where(User.email == em))).scalar()
    if user is None or user.status != "ACTIVE" or not verify_password(password, user.password_hash):
        raise AuthError("invalid credentials", "invalid_credentials")
    return user


async def user_memberships(session: AsyncSession, user_id: uuid.UUID) -> list[OrganizationMembership]:
    return list((await session.execute(
        select(OrganizationMembership).where(OrganizationMembership.user_id == user_id)
    )).scalars().all())
