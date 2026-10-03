"""Record shadow control-policy decisions after Change Guard persists a check."""
from __future__ import annotations

import uuid
from typing import Any

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.changeguard.decision import Decision
from app.control_policy.baseline import BaselinePolicy
from app.control_policy.encoder import DomainContext, encode
from app.control_policy.laya_prior import laya_control_prior
from app.control_policy.learner import POLICY_VERSION, get_control_learner, extended_feature_names
from app.control_policy.mask import compute_mask
from app.core.config import get_settings
from app.models.changeguard import ChangeCheck, ChangeSet
from app.models.control_policy import ControlPolicyDecision

log = structlog.get_logger()


def _mode() -> str:
    return (get_settings().control_policy_mode or "SHADOW").upper()


async def record_shadow_decision(
    session: AsyncSession,
    *,
    change_set: ChangeSet,
    change_check: ChangeCheck,
) -> ControlPolicyDecision | None:
    mode = _mode()
    if mode == "BASELINE_ONLY":
        return None

    existing = (
        await session.execute(
            select(ControlPolicyDecision).where(
                ControlPolicyDecision.change_check_id == change_check.id,
                ControlPolicyDecision.mode == "SHADOW",
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    baseline = Decision(change_check.decision)
    digest_valid = bool(change_check.action_digest)
    mask = compute_mask(change_check.findings or [], digest_valid=digest_valid)
    eligible = tuple(a.value for a in mask.eligible)

    domain = DomainContext(
        action_type=change_set.action_type or "",
        source_mode=change_set.source_mode or "LIVE",
        claims_count=len(change_set.proposed_claims or []),
        reversible=change_set.reversible,
        risk=change_set.risk,
        finding_types=[f.get("type", "") for f in (change_check.findings or []) if isinstance(f, dict)],
        baseline_decision=baseline,
    )
    encoded = encode(domain, graph=None)
    laya = laya_control_prior(
        baseline=baseline,
        action_type=domain.action_type,
        source_mode=domain.source_mode,
        claims_count=domain.claims_count,
        risk=domain.risk,
        findings=change_check.findings or [],
    )
    laya_dist = laya.distribution if laya.available else None

    learner = get_control_learner()
    rec = learner.recommend(encoded.vector, eligible=eligible, laya_distribution=laya_dist)

    returned = baseline.value
    if mode == "ACTIVE":
        returned = rec.action

    agrees = rec.action == baseline.value
    mask_violation = rec.action not in eligible

    row = ControlPolicyDecision(
        id=uuid.uuid4(),
        org_id=change_set.org_id,
        change_set_id=change_set.id,
        change_check_id=change_check.id,
        mode="SHADOW" if mode != "ACTIVE" else "ACTIVE",
        policy_version_id=None,
        algorithm=rec.algorithm,
        baseline_decision=baseline.value,
        recommended_action=rec.action,
        returned_decision=returned,
        eligible_actions=list(eligible),
        masked_actions=mask.to_json()["masked"],
        scores=[s.to_json() for s in rec.scores],
        feature_schema=encoded.schema,
        feature_vector=encoded.vector,
        feature_names=list(extended_feature_names()),
        context_hash=encoded.hash,
        graph_feature_version=encoded.graph_version,
        graph_context_hash=encoded.graph_context_hash,
        graph_snapshot_time=encoded.graph_snapshot_time,
        graph_source=encoded.graph_source,
        graph_stale=False,
        graph_missing=encoded.graph_missing,
        fallback_reason=None if encoded.graph_available else "graph_not_fetched",
        mask_violation=mask_violation,
        agrees=agrees,
        laya_model_version=laya.model_version,
        laya_selected_action=laya.selected,
        laya_distribution=dict(laya.distribution),
        laya_confidence=laya.confidence if laya.available else None,
        laya_escalation_probability=laya.escalation_probability,
    )
    session.add(row)
    await session.flush()
    if mask_violation:
        log.error("control_policy.mask_violation", recommended=rec.action, eligible=eligible)
    return row


async def record_after_check(
    session: AsyncSession,
    change_set: ChangeSet,
    change_check: ChangeCheck,
) -> None:
    if not get_settings().control_policy_shadow_enabled:
        return
    try:
        await record_shadow_decision(session, change_set=change_set, change_check=change_check)
    except Exception as exc:  # noqa: BLE001 — shadow must never break Change Guard
        log.warning("control_policy.shadow_failed", error=repr(exc))
