"""Human review records. Rejection reasons are stored for inspection. They are not rewards."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ApprovalStatus
from app.models.interventions import Approval

REJECTION_REASONS = (
    "incorrect_root_cause",
    "action_too_risky",
    "action_too_costly",
    "already_addressed",
    "insufficient_evidence",
    "not_strategically_important",
    "other",
)


class UnknownRejectionReason(ValueError):
    pass


def normalize_reason(code: str | None) -> str:
    if not code or not code.strip():
        return "other"
    cleaned = code.strip()
    if cleaned not in REJECTION_REASONS:
        raise UnknownRejectionReason(cleaned)
    return cleaned


def acceptance_from_counts(granted: int, rejected: int) -> tuple[float | None, int]:
    decided = granted + rejected
    if decided == 0:
        return None, 0
    return granted / decided, decided


async def acceptance_rate(session: AsyncSession) -> tuple[float | None, int]:
    rows = (await session.execute(select(Approval.status))).all()
    granted = rejected = 0
    for (status,) in rows:
        value = status.value if hasattr(status, "value") else str(status)
        if value in (ApprovalStatus.APPROVED.value, ApprovalStatus.MODIFIED.value):
            granted += 1
        elif value == ApprovalStatus.REJECTED.value:
            rejected += 1
    return acceptance_from_counts(granted, rejected)
