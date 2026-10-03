"""Control-policy tables (docs/GRAPH_SPEC.md): immutable policy versions, append-only decisions (shadow + active),
append-only feedback and offline-evaluation records.

Guards are ORM-level (B1 style) AND, on Postgres, row triggers installed by migration 0007: a raw UPDATE/DELETE on any
of these tables is refused by the database. History is never mutated; a "change" is a new row.
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow

JSONType = JSON().with_variant(JSONB(), "postgresql")

DECISIONS = ("ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK")
MODES = ("SHADOW", "ACTIVE", "BASELINE_ONLY")
FEEDBACK_KINDS = ("human", "outcome")


def _in(col: str, values: tuple[str, ...]) -> str:
    return f"{col} IN ({', '.join(repr(v) for v in values)})"


class ImmutableControlPolicyError(Exception):
    """A control-policy row was updated or deleted."""


class ControlPolicyVersion(Base):
    """Immutable: algorithm, feature schema, hyperparameters, training event count, parent, created_at (+ the learner
    state snapshot at creation so any recommendation is reproducible from persisted data)."""

    __tablename__ = "control_policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(64), unique=True)
    algorithm: Mapped[str] = mapped_column(String(48))
    feature_schema: Mapped[str] = mapped_column(String(32))
    hyperparameters: Mapped[dict] = mapped_column(JSONType, default=dict)
    training_event_count: Mapped[int] = mapped_column(Integer, default=0)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("control_policy_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    reward_config_version: Mapped[str] = mapped_column(String(32), default="")
    state_snapshot: Mapped[dict] = mapped_column(JSONType, default=dict)
    created_by: Mapped[str] = mapped_column(String(255), default="system")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ControlPolicyDecision(Base):
    """One row per (change check, mode): the baseline decision, the bandit recommendation, scores, eligible actions and
    the exact feature vector/schema/hash/snapshot the recommendation was computed from (never recomputed later)."""

    __tablename__ = "control_policy_decisions"
    __table_args__ = (
        UniqueConstraint("change_check_id", "mode", name="uq_cpd_check_mode"),
        Index("ix_cpd_org_created", "org_id", "created_at"),
        CheckConstraint(_in("mode", MODES), name="ck_cpd_mode"),
        CheckConstraint(_in("baseline_decision", DECISIONS), name="ck_cpd_baseline"),
        CheckConstraint(_in("returned_decision", DECISIONS), name="ck_cpd_returned"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    org_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id", ondelete="RESTRICT"), index=True)
    change_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("change_sets.id", ondelete="RESTRICT"), index=True)
    change_check_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("change_checks.id", ondelete="RESTRICT"))
    mode: Mapped[str] = mapped_column(String(16))
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("control_policy_versions.id", ondelete="RESTRICT"), nullable=True, index=True)
    algorithm: Mapped[str | None] = mapped_column(String(48), nullable=True)
    baseline_decision: Mapped[str] = mapped_column(String(16))
    recommended_action: Mapped[str | None] = mapped_column(String(16), nullable=True)
    returned_decision: Mapped[str] = mapped_column(String(16))  # what the caller received (baseline unless ACTIVE)
    eligible_actions: Mapped[list] = mapped_column(JSONType, default=list)
    masked_actions: Mapped[dict] = mapped_column(JSONType, default=dict)
    scores: Mapped[list] = mapped_column(JSONType, default=list)
    feature_schema: Mapped[str | None] = mapped_column(String(32), nullable=True)
    feature_vector: Mapped[list] = mapped_column(JSONType, default=list)
    feature_names: Mapped[list] = mapped_column(JSONType, default=list)
    context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph_feature_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    graph_context_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    graph_snapshot_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    graph_source: Mapped[str | None] = mapped_column(String(24), nullable=True)
    graph_stale: Mapped[bool] = mapped_column(Boolean, default=False)
    graph_missing: Mapped[list] = mapped_column(JSONType, default=list)
    fallback_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    mask_violation: Mapped[bool] = mapped_column(Boolean, default=False)
    agrees: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # recommendation == baseline decision
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ControlPolicyFeedback(Base):
    """Append-only human/outcome feedback linked to the decision made at t0. Override dimensions are stored
    separately; a disagreement is not automatically an error. `settled` marks the single row that carries the
    learnable reward for the decision (exactly-once)."""

    __tablename__ = "control_policy_feedback"
    __table_args__ = (
        UniqueConstraint("decision_id", "kind", name="uq_cpf_decision_kind"),
        Index("uq_cpf_one_settled", "decision_id", unique=True,
              postgresql_where=text("settled"), sqlite_where=text("settled")),
        CheckConstraint(_in("kind", FEEDBACK_KINDS), name="ck_cpf_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    decision_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control_policy_decisions.id", ondelete="RESTRICT"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    policy_recommendation: Mapped[str | None] = mapped_column(String(16), nullable=True)
    human_decision: Mapped[str | None] = mapped_column(String(16), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    final_execution: Mapped[str | None] = mapped_column(String(16), nullable=True)
    eventual_outcome: Mapped[str | None] = mapped_column(String(48), nullable=True)
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    reward: Mapped[float | None] = mapped_column(Float, nullable=True)
    settled: Mapped[bool] = mapped_column(Boolean, default=False)
    reward_config_version: Mapped[str] = mapped_column(String(32), default="")
    actor: Mapped[str] = mapped_column(String(255), default="system")
    decision_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # t0
    outcome_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)  # t+n
    delay_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ControlPolicyEvaluation(Base):
    """Append-only record of an offline evaluation of a policy version (input to the graduation gate)."""

    __tablename__ = "control_policy_evaluations"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    policy_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("control_policy_versions.id", ondelete="RESTRICT"), index=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    data_kind: Mapped[str] = mapped_column(String(24), default="SYNTHETIC")  # SYNTHETIC | REPLAY | REAL
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    report_ref: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


for _model in (ControlPolicyVersion, ControlPolicyDecision, ControlPolicyFeedback, ControlPolicyEvaluation):
    @event.listens_for(_model, "before_update")
    @event.listens_for(_model, "before_delete")
    def _immutable(mapper, connection, target) -> None:
        raise ImmutableControlPolicyError(f"{type(target).__tablename__} rows are append-only / immutable")
