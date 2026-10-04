"""Change Guard tables: immutable ChangeSets, append-only ChangeChecks, org-managed canonical claims.

Guards are ORM-level (B1 style) AND, on Postgres, row triggers installed by migration 0004 (a raw UPDATE/DELETE on
`change_sets` / `change_checks` is refused by the database too).
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")

DECISIONS = ("ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK")
SOURCE_MODES = ("LIVE", "SIMULATED")
ORIGINS = ("external", "intervention")
SEMANTIC_STATES = ("ok", "degraded", "skipped_no_canonical_truth")
CLAIM_STATUSES = ("active", "retired")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class ImmutableChangeGuardError(Exception):
    """A ChangeSet / ChangeCheck row was updated or deleted."""


class ChangeSet(Base):
    """Immutable proposal posted by an agent (origin=external) or built from Profound Lift's own intervention."""

    __tablename__ = "change_sets"
    __table_args__ = (
        UniqueConstraint("org_id", "idempotency_key", name="uq_change_sets_org_idem"),
        Index("ix_change_sets_org_target", "org_id", "target_key"),
        CheckConstraint(_in("source_mode", SOURCE_MODES), name="ck_change_sets_source_mode"),
        CheckConstraint(_in("origin", ORIGINS), name="ck_change_sets_origin"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    origin: Mapped[str] = mapped_column(String(16), default="external")
    intervention_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("interventions.id", ondelete="RESTRICT"), nullable=True, index=True)
    agent_id: Mapped[str] = mapped_column(String(128))
    agent_name: Mapped[str] = mapped_column(String(255), default="")
    profound_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    source_mode: Mapped[str] = mapped_column(String(16), default="LIVE")
    target_url: Mapped[str | None] = mapped_column(Text, nullable=True)  # as given
    target_key: Mapped[str] = mapped_column(String(1024), default="")  # normalized url ('' = no page target)
    action_type: Mapped[str] = mapped_column(String(48))
    proposed_claims: Mapped[list] = mapped_column(JSONType, default=list)  # normalized, sorted, de-duplicated
    claims_raw: Mapped[list] = mapped_column(JSONType, default=list)  # as given
    prompt_cluster_ids: Mapped[list] = mapped_column(JSONType, default=list)
    prompts: Mapped[list] = mapped_column(JSONType, default=list)
    proposed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    expected_kpi: Mapped[str | None] = mapped_column(String(255), nullable=True)
    risk: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reversible: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    idempotency_key: Mapped[str] = mapped_column(String(255))
    proposal_digest: Mapped[str] = mapped_column(String(64), index=True)  # sha256 of the proposal (no experiment context)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ChangeCheck(Base):
    """One row per evaluation (append-only). The decision, ALL findings and the guard version are stored."""

    __tablename__ = "change_checks"
    __table_args__ = (
        Index("ix_change_checks_org_created", "org_id", "created_at"),
        CheckConstraint(_in("decision", DECISIONS), name="ck_change_checks_decision"),
        CheckConstraint(_in("semantic_check", SEMANTIC_STATES), name="ck_change_checks_semantic"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    change_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("change_sets.id", ondelete="RESTRICT"), index=True)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    decision: Mapped[str] = mapped_column(String(16), index=True)
    findings: Mapped[list] = mapped_column(JSONType, default=list)
    eligible_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    merged_proposal: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    semantic_check: Mapped[str] = mapped_column(String(32))
    guard_version: Mapped[str] = mapped_column(String(32))
    action_digest: Mapped[str] = mapped_column(String(64), index=True)  # sha256 incl. experiment context at check time
    experiment_context: Mapped[list] = mapped_column(JSONType, default=list)
    experiment_refs: Mapped[list] = mapped_column(JSONType, default=list)  # experiment ids + codes named by findings
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CanonicalClaim(TimestampMixin, Base):
    """Admin-managed source of truth for an organization. Retired, never deleted."""

    __tablename__ = "canonical_claims"
    __table_args__ = (
        Index("uq_canonical_claims_active_key", "org_id", "key", unique=True,
              postgresql_where=text("status = 'active'"), sqlite_where=text("status = 'active'")),
        CheckConstraint(_in("status", CLAIM_STATUSES), name="ck_canonical_claims_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    key: Mapped[str] = mapped_column(String(128))
    statement: Mapped[str] = mapped_column(Text)
    entities: Mapped[list] = mapped_column(JSONType, default=list)
    scope: Mapped[str | None] = mapped_column(String(512), nullable=True)
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    valid_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str | None] = mapped_column(String(512), nullable=True)  # provenance (doc / URL / owner)
    status: Mapped[str] = mapped_column(String(16), default="active")
    created_by: Mapped[str] = mapped_column(String(255), default="operator")
    updated_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    retired_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    retire_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


@event.listens_for(ChangeSet, "before_update")
@event.listens_for(ChangeSet, "before_delete")
def _change_set_immutable(mapper, connection, target) -> None:
    raise ImmutableChangeGuardError("change_sets are immutable")


@event.listens_for(ChangeCheck, "before_update")
@event.listens_for(ChangeCheck, "before_delete")
def _change_check_append_only(mapper, connection, target) -> None:
    raise ImmutableChangeGuardError("change_checks are append-only")


@event.listens_for(CanonicalClaim, "before_delete")
def _claim_no_delete(mapper, connection, target) -> None:
    raise ImmutableChangeGuardError("canonical claims are retired, never deleted")


@event.listens_for(CanonicalClaim, "before_update")
def _claim_retired_is_final(mapper, connection, target) -> None:
    from sqlalchemy import inspect

    hist = inspect(target).attrs.status.history
    prior = list(hist.deleted) + list(hist.unchanged)
    if prior and prior[0] == "retired":
        raise ImmutableChangeGuardError("a retired canonical claim cannot be changed; add a new claim")
