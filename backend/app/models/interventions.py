"""Intervention, approval, execution and experiment-ledger tables."""
import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    event,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base, TimestampMixin, utcnow
from app.domain.enums import ActionType, ApprovalStatus, ExperimentStatus, Risk, SelectionBasis

JSONType = JSON().with_variant(JSONB(), "postgresql")


def _enum(cls: type) -> Enum:
    return Enum(cls, native_enum=False, length=32, values_callable=lambda e: [m.value for m in e],
                validate_strings=True)


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


class ImmutableApprovalError(Exception):
    """Raised by the ORM guard when a decided approval is modified."""


class Intervention(TimestampMixin, Base):
    __tablename__ = "interventions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="CASCADE"), index=True)
    hypothesis_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("hypotheses.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[ActionType] = mapped_column(_enum(ActionType))
    title: Mapped[str] = mapped_column(String(512))
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk: Mapped[Risk] = mapped_column(_enum(Risk), default=Risk.LOW)
    proposed_change: Mapped[dict] = mapped_column(JSONType, default=dict)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    selected: Mapped[bool] = mapped_column(Boolean, default=False)
    selection_basis: Mapped[SelectionBasis | None] = mapped_column(_enum(SelectionBasis), nullable=True)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )


class Approval(TimestampMixin, Base):
    """Human authorization record. Immutable once `status != pending`."""

    __tablename__ = "approvals"
    __table_args__ = (
        # Two concurrent requests cannot both open a PENDING approval for one intervention.
        Index("uq_approvals_one_pending", "intervention_id", unique=True,
              postgresql_where=text("status = 'pending'"), sqlite_where=text("status = 'pending'")),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interventions.id", ondelete="RESTRICT"), index=True
    )
    status: Mapped[ApprovalStatus] = mapped_column(_enum(ApprovalStatus), default=ApprovalStatus.PENDING)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    modified_change: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    # extras beyond the contract
    requested_by: Mapped[str] = mapped_column(String(255), default="system")
    requested_actor_type: Mapped[str] = mapped_column(String(16), default="system")
    decided_actor_type: Mapped[str | None] = mapped_column(String(16), nullable=True)  # human|model|system
    requested_change: Mapped[dict] = mapped_column(JSONType, default=dict)  # snapshot at request time
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Change Guard: sha256 of the exact change that was approved (set with the decision; see app/changeguard/digest.py).
    action_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)


@event.listens_for(Approval, "before_update")
def _approval_immutable(mapper, connection, target: Approval) -> None:
    hist = inspect(target).attrs.status.history
    prior = list(hist.deleted) + list(hist.unchanged)
    if prior and any(p != ApprovalStatus.PENDING for p in prior):
        raise ImmutableApprovalError(f"approval {target.id} is already {prior[0]} and cannot be changed")


class Execution(TimestampMixin, Base):
    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = _uuid_pk()
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interventions.id", ondelete="RESTRICT"), index=True
    )
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True, index=True
    )
    executor: Mapped[str] = mapped_column(String(64), default="manual")
    # planned (github dry-run preview) | awaiting_human_execution (manual package issued) | succeeded | failed
    status: Mapped[str] = mapped_column(String(32), default="pending")
    reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)  # reference URL (PR, live page, ...)
    # dry_run=True ONLY for a GitHub-executor preview. A manual execution is a real execution (dry_run=False).
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    package: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # manual intervention package
    actual_change: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # what the human really applied
    deviation: Mapped[bool] = mapped_column(Boolean, default=False)  # human applied something other than proposed
    executed_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    log: Mapped[list] = mapped_column(JSONType, default=list)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


experiment_number_seq = Sequence("experiment_number_seq", metadata=Base.metadata)


class Experiment(TimestampMixin, Base):
    __tablename__ = "experiments"

    id: Mapped[uuid.UUID] = _uuid_pk()
    number: Mapped[int] = mapped_column(Integer, experiment_number_seq, unique=True, index=True)
    # RESTRICT everywhere on experiment history: an incident/intervention with experiments can never be deleted.
    incident_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("incidents.id", ondelete="RESTRICT"), index=True)
    intervention_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("interventions.id", ondelete="RESTRICT"), index=True
    )
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    context_vector: Mapped[list | dict] = mapped_column(JSONType, default=list)
    alternatives: Mapped[list] = mapped_column(JSONType, default=list)
    evidence_snapshot: Mapped[dict | list] = mapped_column(JSONType, default=dict)
    before_metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    after_metrics: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    status: Mapped[ExperimentStatus] = mapped_column(
        _enum(ExperimentStatus), default=ExperimentStatus.PROPOSED, index=True
    )
    verification_window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    verification_window_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    executed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # extras: full ledger record
    selected_action: Mapped[ActionType] = mapped_column(_enum(ActionType))
    selection_basis: Mapped[SelectionBasis | None] = mapped_column(_enum(SelectionBasis), nullable=True)
    cold_start: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    reason: Mapped[str] = mapped_column(Text, default="")
    proposed_change: Mapped[dict] = mapped_column(JSONType, default=dict)
    approved_change: Mapped[dict | None] = mapped_column(JSONType, nullable=True)  # effective change post-approval
    approval_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("approvals.id", ondelete="SET NULL"), nullable=True
    )
    approver: Mapped[str | None] = mapped_column(String(255), nullable=True)
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL"), nullable=True
    )
    executor: Mapped[str | None] = mapped_column(String(64), nullable=True)
    execution_reference: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    dry_run: Mapped[bool] = mapped_column(Boolean, default=False)
    timeline: Mapped[list] = mapped_column(JSONType, default=list)  # status transitions
    # Pre-activation declaration (if action, because root cause, then metric...); immutable after activation.
    spec: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    policy_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_decisions.id", ondelete="SET NULL"), nullable=True, index=True
    )
    policy_action: Mapped[str | None] = mapped_column(String(48), nullable=True)  # what the policy chose
    override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    override_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_key: Mapped[str | None] = mapped_column(String(512), nullable=True, index=True)

    @property
    def code(self) -> str:
        return f"EXP-{self.number:04d}" if self.number is not None else "EXP-????"


class Observation(TimestampMixin, Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_observations_experiment_observed", "experiment_id", "observed_at"),
        # The same external measurement (experiment, provider, provider run id, snapshot time) is stored once.
        UniqueConstraint("experiment_id", "source", "source_run_id", "observed_at", name="uq_observations_source_snapshot"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("experiments.id", ondelete="RESTRICT"), index=True)
    metrics: Mapped[dict] = mapped_column(JSONType, default=dict)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(64), default="profound")
    # Provider run/report id; "" when the provider supplies none (keeps the unique key NULL-free).
    source_run_id: Mapped[str] = mapped_column(String(128), default="", server_default="")


class Reward(TimestampMixin, Base):
    __tablename__ = "rewards"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="RESTRICT"), unique=True, index=True
    )
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    total: Mapped[float] = mapped_column(Float)
    weights: Mapped[dict] = mapped_column(JSONType, default=dict)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


@event.listens_for(Experiment, "before_insert")
def _fill_experiment_number(mapper, connection, target: Experiment) -> None:
    """Assign `number` client-side so it is loaded after flush: nextval on Postgres, max+1 elsewhere (sqlite)."""
    if target.number is not None:
        return
    if connection.dialect.name == "postgresql":
        target.number = connection.execute(select(experiment_number_seq.next_value())).scalar_one()
    else:
        target.number = (connection.execute(select(func.max(Experiment.number))).scalar() or 0) + 1


class ExperimentOutcome(TimestampMixin, Base):
    """One append-only outcome row per experiment (requested by the experiments/learning layer)."""

    __tablename__ = "experiment_outcomes"

    id: Mapped[uuid.UUID] = _uuid_pk()
    experiment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("experiments.id", ondelete="RESTRICT"), unique=True, index=True
    )
    outcome: Mapped[str] = mapped_column(String(16))  # favorable|unfavorable|neutral|inconclusive
    observe_outcome: Mapped[str | None] = mapped_column(String(24), nullable=True)
    reward_total: Mapped[float | None] = mapped_column(Float, nullable=True)  # NULL = no policy update
    components: Mapped[dict] = mapped_column(JSONType, default=dict)
    confounders: Mapped[list] = mapped_column(JSONType, default=list)
    causal_confidence: Mapped[str] = mapped_column(String(16), default="low")
    learning_applied: Mapped[bool] = mapped_column(Boolean, default=False)
    policy_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("policy_versions.id", ondelete="SET NULL"), nullable=True
    )
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    methodology: Mapped[dict] = mapped_column(JSONType, default=dict)


class ImmutableExperimentError(Exception):
    """Raised when a frozen experiment field is changed after activation."""


_S = ExperimentStatus
# Frozen the moment the experiment leaves PROPOSED (activation): the whole decision + baseline snapshot.
_FROZEN_AFTER_ACTIVATION = (
    "incident_id", "intervention_id", "policy_version_id", "policy_probability", "context_vector", "alternatives",
    "evidence_snapshot", "before_metrics", "selected_action", "selection_basis", "cold_start", "proposed_change",
    "approved_change", "approval_id", "approver", "spec", "policy_decision_id", "policy_action", "override_reason",
    "override_by", "target_key",
)
_EXECUTION_FIELDS = ("execution_id", "executor", "execution_reference", "dry_run", "executed_at")
_WINDOW_FIELDS = ("verification_window_start", "verification_window_end")
_AFTER_EXEC = {_S.EXECUTED, _S.AWAITING_VERIFICATION, _S.VERIFIED, _S.REWARDED}
_AFTER_WINDOW = {_S.AWAITING_VERIFICATION, _S.VERIFIED, _S.REWARDED}


def _prior(target, name: str, connection=None):
    """(known, old, new, changed) for an attribute. If the old value was never loaded (e.g. an unset column on a
    freshly inserted row) it is read from the database so a change can never slip through unnoticed."""
    hist = inspect(target).attrs[name].history
    if not hist.has_changes() or not hist.added:
        return False, None, None, False
    new = hist.added[0]
    if hist.deleted:
        old = hist.deleted[0]
    elif connection is not None:
        row = connection.execute(select(Experiment.__table__.c[name]).where(Experiment.__table__.c.id == target.id)).first()
        if row is None:
            return False, None, None, False
        old = row[0]
    else:
        return False, None, None, False
    return True, old, new, _norm(old) != _norm(new)


def _norm(v):
    return getattr(v, "value", v)


def _check_transition_chain(target, prior, new, connection) -> None:
    """The status may only change through `experiments.status.transition`: its timeline entries appended since the
    last flush must form an allowed chain prior -> ... -> new (several transitions may share one flush)."""
    from app.experiments.status import ALLOWED, IllegalExperimentTransition

    known, old_tl, new_tl, _ = _prior(target, "timeline", connection)
    appended = list((new_tl or [])[len(old_tl or []):]) if known else []
    cur = prior
    for step in appended:
        try:
            nxt = ExperimentStatus(step.get("to"))
            frm = ExperimentStatus(step.get("from")) if step.get("from") else None
        except (ValueError, AttributeError):
            break
        if frm != cur or nxt not in ALLOWED[cur]:
            break
        cur = nxt
    if cur != new:
        raise IllegalExperimentTransition(
            f"experiment {target.id}: {prior.value} -> {new.value} did not go through the transition logic"
        )


@event.listens_for(Experiment, "before_update")
def _experiment_frozen(mapper, connection, target: Experiment) -> None:
    """ORM guard: snapshots, policy context, action, baseline and window cannot change after activation."""
    hist = inspect(target).attrs.status.history
    prior_list = list(hist.deleted) + list(hist.unchanged)
    if not prior_list:
        return
    prior = ExperimentStatus(prior_list[0])
    if hist.has_changes() and hist.added and ExperimentStatus(hist.added[0]) != prior:
        _check_transition_chain(target, prior, ExperimentStatus(hist.added[0]), connection)
    if prior != _S.PROPOSED:
        for name in _FROZEN_AFTER_ACTIVATION:
            if _prior(target, name, connection)[3]:
                raise ImmutableExperimentError(
                    f"experiment {target.id}: {name} is frozen after activation (status was {prior.value})"
                )
    if prior in _AFTER_EXEC:
        for name in _EXECUTION_FIELDS:
            if _prior(target, name, connection)[3]:
                raise ImmutableExperimentError(f"experiment {target.id}: {name} is frozen once executed")
    if prior in _AFTER_WINDOW:
        for name in _WINDOW_FIELDS:
            if _prior(target, name, connection)[3]:
                raise ImmutableExperimentError(f"experiment {target.id}: {name} is frozen once verification started")
    known, old, new, changed = _prior(target, "after_metrics", connection)
    if known and changed and old:  # write-once: None -> value only
        raise ImmutableExperimentError(f"experiment {target.id}: after_metrics is write-once")


@event.listens_for(Experiment, "before_delete")
def _experiment_no_delete(mapper, connection, target: Experiment) -> None:
    raise ImmutableExperimentError("experiment history is never deleted")


@event.listens_for(ExperimentOutcome, "before_update")
def _outcome_append_only(mapper, connection, target) -> None:
    raise ImmutableExperimentError("experiment outcomes are append-only")


@event.listens_for(Reward, "before_update")
def _reward_append_only(mapper, connection, target) -> None:
    raise ImmutableExperimentError("rewards are append-only")
