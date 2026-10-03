"""Persistence for policy versions and decisions.

Versions are append-only: `save_update` / `save_new_version` always INSERT a new `PolicyVersion`
(v0.0.1 -> v0.0.2 -> ...) whose `parent_id` is the version it was derived from. Existing rows are never
modified (and the ORM blocks it, see `app.models.policy`).
"""
from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import Mapping
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, SelectionBasis
from app.models.policy import PolicyDecision, PolicyVersion
from app.policy.bandit import POLICY_CLASSES, Decision, LinUCBPolicy, Policy, PolicyConfig, policy_from_state
from app.policy.features import SCHEMA, coerce_context
from app.policy.priors import COLD_START_PRIORS, priors_to_json

INITIAL_VERSION = "v0.0.1"
_VER = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def next_version(prev: str | None) -> str:
    """v0.0.1 -> v0.0.2 (patch bump). None -> v0.0.1."""
    if prev is None:
        return INITIAL_VERSION
    m = _VER.match(prev)
    if not m:
        raise ValueError(f"bad version string {prev!r}")
    a, b, c = (int(g) for g in m.groups())
    return f"v{a}.{b}.{c + 1}"


def prior_config_hash(rules: Any, config: Mapping[str, Any]) -> str:
    """Stable sha256 of the explicit prior rules + hyperparameters in force (what a version is parameterised by)."""
    blob = json.dumps({"priors": priors_to_json(rules), "config": dict(config)}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def version_meta(policy: Policy, parent: PolicyVersion | None) -> dict[str, Any]:
    """Immutable descriptor stored inside `PolicyVersion.priors["meta"]`: algorithm, hyperparameters, parent,
    rewarded-experiment count, prior hash, feature schema. Cold start is stated, not dressed up as expertise."""
    cfg = policy.config.to_json()
    return {
        "algorithm": policy.algorithm, "hyperparameters": cfg, "parent_version": parent.version if parent else None,
        "parent_id": str(parent.id) if parent else None, "rewarded_experiments": policy.n_updates,
        "prior_config_hash": prior_config_hash(policy.rules, cfg), "feature_schema": policy.feature_schema,
        "cold_start": policy.n_updates == 0,
        "note": ("cold start: hand-set explicit priors only; this version has learned nothing from outcomes"
                 if policy.n_updates == 0 else
                 f"learned from {policy.n_updates} rewarded experiment(s) on top of the explicit priors"),
    }


class PolicyStore:
    def __init__(self, session: AsyncSession, algorithm: str = "linucb", config: PolicyConfig | None = None):
        if algorithm not in POLICY_CLASSES:
            raise ValueError(f"unknown algorithm {algorithm!r}")
        self.session = session
        self.algorithm = algorithm
        self.config = config or PolicyConfig()

    # ---- versions --------------------------------------------------------------------------------
    async def latest(self) -> PolicyVersion | None:
        q = select(PolicyVersion).order_by(PolicyVersion.n_updates.desc(), PolicyVersion.created_at.desc(),
                                           PolicyVersion.version.desc()).limit(1)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def get(self, version: str | uuid.UUID) -> PolicyVersion | None:
        col = PolicyVersion.id if isinstance(version, uuid.UUID) else PolicyVersion.version
        return (await self.session.execute(select(PolicyVersion).where(col == version))).scalar_one_or_none()

    async def list_versions(self) -> list[PolicyVersion]:
        q = select(PolicyVersion).order_by(PolicyVersion.n_updates, PolicyVersion.created_at)
        return list((await self.session.execute(q)).scalars())

    async def ensure_initial(self) -> PolicyVersion:
        """Latest version, creating the cold-start v0.0.1 (zero observations) if none exists."""
        latest = await self.latest()
        if latest is not None:
            return latest
        policy = POLICY_CLASSES[self.algorithm](self.config, version=INITIAL_VERSION)
        return await self._insert(policy, INITIAL_VERSION, None, None)

    async def _insert(self, policy: Policy, version: str, parent: PolicyVersion | None,
                      experiment_id: uuid.UUID | None) -> PolicyVersion:
        row = PolicyVersion(
            version=version, algorithm=policy.algorithm, state=policy.to_state(),
            priors=priors_to_json(policy.rules) | {"config": policy.config.to_json(),
                                                   "meta": version_meta(policy, parent)},
            n_updates=policy.n_updates, parent_id=parent.id if parent else None,
            source_experiment_id=experiment_id, immutable=True,
        )
        self.session.add(row)
        await self.session.flush()
        policy.version = version
        policy.version_id = row.id
        return row

    async def load_policy(self, version: str | uuid.UUID | PolicyVersion | None = None) -> Policy:
        row = version if isinstance(version, PolicyVersion) else (
            await self.get(version) if version is not None else await self.ensure_initial())
        if row is None:
            raise LookupError(f"policy version {version!r} not found")
        pol = policy_from_state(row.algorithm, row.state, version=row.version, rules=COLD_START_PRIORS)
        pol.version_id = row.id
        return pol

    async def save_new_version(self, policy: Policy, parent: PolicyVersion,
                               experiment_id: uuid.UUID | None = None) -> PolicyVersion:
        return await self._insert(policy, next_version(parent.version), parent, experiment_id)

    async def save_update(self, parent: PolicyVersion, context: Any, action: Any, reward: float,
                          experiment_id: uuid.UUID | None = None) -> PolicyVersion:
        """Apply one (context, action, reward) to a COPY of `parent`'s state and persist it as a new version."""
        policy = await self.load_policy(parent)
        policy.upgrade_schema()  # an older feature schema is zero-padded into the current one (recorded in meta)
        policy.update(context, action, reward)
        return await self.save_new_version(policy, parent, experiment_id)

    # ---- decisions -------------------------------------------------------------------------------
    async def record_decision(self, decision: Decision, policy: Policy, incident_id: uuid.UUID | None = None,
                              meta: dict[str, Any] | None = None) -> PolicyDecision:
        """Persist everything needed to reproduce the decision from (policy version, context, eligibility, draw):
        context vector + feature schema, eligible and masked actions (with reasons), per-action scores, the selected
        action, propensity, the uniform draw used to select, hyperparameters and the policy version."""
        ctx = decision.full_context if decision.full_context is not None else decision.context
        row = PolicyDecision(
            incident_id=incident_id,
            policy_version_id=policy.version_id,
            context_vector=_ctx_json(ctx),
            scores=decision.scores_json(),
            selected_action=decision.action.value,
            probability=decision.probability,
            selection_basis=decision.selection_basis.value,
            cold_start=decision.cold_start,
            n_related=decision.n_related,
            allowed_actions=[a.value for a in decision.allowed_actions],
            meta={"matched_rules": decision.matched_rules, "algorithm": decision.algorithm,
                  "note": decision.note, "policy_version": decision.policy_version,
                  "feature_schema": decision.feature_schema,
                  "eligible_actions": [a.value for a in decision.allowed_actions],
                  "masked_actions": dict(decision.masked_actions),
                  "selection": {"method": "inverse_cdf_over_probabilities", "uniform_draw": decision.uniform_draw},
                  "hyperparameters": policy.config.to_json(), **(meta or {})},
        )
        self.session.add(row)
        await self.session.flush()
        decision.policy_decision_id = row.id
        return row

    async def load_allowed_actions(self) -> dict[str, bool] | None:
        """Operator mask from the `settings` table (key "policy", value {"allowed_actions": {...}}), if set."""
        from app.models.core import Setting

        row = await self.session.get(Setting, "policy")
        mask = (row.value or {}).get("allowed_actions") if row is not None else None
        return mask or None

    async def decide(self, context: Any, incident_id: uuid.UUID | None = None,
                     policy: Policy | None = None, *, eligible: Any = None,
                     masked: Mapping[str, str] | None = None) -> tuple[Decision, PolicyDecision]:
        """Select an action with the latest version and log the decision (with propensity)."""
        policy = policy or await self.load_policy()
        mask = await self.load_allowed_actions()
        if mask is not None:
            policy.config.allowed_actions = mask
        decision = policy.select(context, eligible=eligible, masked=masked)
        return decision, await self.record_decision(decision, policy, incident_id)


def _ctx_json(ctx: Any) -> dict[str, Any]:
    from app.policy.features import FEATURE_NAMES

    if ctx is None:
        return {}
    x = coerce_context(ctx)
    return {"schema": SCHEMA, "names": list(FEATURE_NAMES), "values": [float(v) for v in x]}


async def reproduce_decision(session: AsyncSession, row: PolicyDecision) -> dict[str, Any]:
    """Recompute a persisted decision from its persisted inputs only (version row + context + eligibility + draw).
    Returns {"match": bool, "action", "probability", "scores"}; `match` is True iff the selected action, the
    propensity and every per-action score are reproduced."""
    store = PolicyStore(session)
    version = await store.get(row.policy_version_id)
    if version is None:
        raise LookupError("policy version of the decision no longer exists")
    policy = await store.load_policy(version)
    meta = row.meta or {}
    policy.config.allowed_actions = (meta.get("hyperparameters") or {}).get("allowed_actions")
    eligible = row.allowed_actions or None
    if meta.get("constrained_to_observe"):  # root cause unconfirmed: scores shown, only `observe` selectable
        sc = policy.score(row.context_vector)
        again = Decision(ActionType.OBSERVE, 1.0, sc, SelectionBasis.RULE_FALLBACK, True, policy.version)
    elif row.selection_basis == "rule_fallback":  # deterministic prior argmax, no draw
        again = policy.rule_fallback(row.context_vector, eligible=eligible)
    else:
        again = policy.select(row.context_vector, eligible=eligible,
                              uniform_draw=(meta.get("selection") or {}).get("uniform_draw"))
    old = {s["action"]: s for s in row.scores}
    same_scores = set(old) == {s.action.value for s in again.scores} and all(
        abs(old[s.action.value]["ucb"] - s.ucb) < 1e-9 for s in again.scores)
    match = (again.action.value == row.selected_action and abs(again.probability - row.probability) < 1e-9
             and same_scores)
    return {"match": match, "action": again.action.value, "probability": again.probability,
            "scores": again.scores_json()}


__all__ = ["PolicyStore", "next_version", "INITIAL_VERSION", "LinUCBPolicy"]
