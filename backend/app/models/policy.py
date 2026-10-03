"""Bandit policy persistence.

`PolicyVersion` rows are immutable: every learning update writes a NEW row (parent_id -> previous),
and any attempt to UPDATE or DELETE an existing row through the ORM raises `ImmutableVersionError`.
`PolicyDecision` is the append-only log of every selection (with its propensity) used for later
off-policy evaluation.
"""
import uuid
from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Uuid, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, utcnow


class ImmutableVersionError(RuntimeError):
    pass


class PolicyVersion(Base):
    __tablename__ = "policy_versions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    version: Mapped[str] = mapped_column(String(32), unique=True, index=True)  # "v0.0.1"
    algorithm: Mapped[str] = mapped_column(String(32))  # "linucb" | "thompson"
    state: Mapped[dict] = mapped_column(JSON, default=dict)  # serialized per-action A, b, counts
    priors: Mapped[dict] = mapped_column(JSON, default=dict)  # cold-start priors + config in force
    n_updates: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("policy_versions.id"), nullable=True, index=True
    )
    # Which experiment/reward produced this version (None for the initial version).
    # UNIQUE + FK: an experiment can produce at most one policy version (exactly-once learning).
    source_experiment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("experiments.id", ondelete="RESTRICT", use_alter=True, name="fk_policy_versions_source_experiment"),
        nullable=True, unique=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    immutable: Mapped[bool] = mapped_column(Boolean, default=True)


class PolicyDecision(Base):
    __tablename__ = "policy_decisions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    # Nullable (decisions may be logged without an incident) but, when set, must reference a real incident.
    incident_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    policy_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("policy_versions.id"), index=True)
    context_vector: Mapped[dict] = mapped_column(JSON, default=dict)  # {"names": [...], "values": [...]}
    scores: Mapped[list] = mapped_column(JSON, default=list)  # list of ActionScore dicts
    selected_action: Mapped[str] = mapped_column(String(48))
    probability: Mapped[float] = mapped_column(Float)  # propensity of selected_action under the policy
    selection_basis: Mapped[str] = mapped_column(String(32))
    cold_start: Mapped[bool] = mapped_column(Boolean, default=True)
    n_related: Mapped[int] = mapped_column(Integer, default=0)
    allowed_actions: Mapped[list] = mapped_column(JSON, default=list)
    meta: Mapped[dict] = mapped_column(JSON, default=dict)  # matched prior rules, fallback reason, ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


@event.listens_for(PolicyVersion, "before_update")
def _block_update(mapper, connection, target):  # noqa: ARG001
    raise ImmutableVersionError("PolicyVersion rows are immutable; create a new version instead")


@event.listens_for(PolicyVersion, "before_delete")
def _block_delete(mapper, connection, target):  # noqa: ARG001
    raise ImmutableVersionError("PolicyVersion rows are immutable and cannot be deleted")
