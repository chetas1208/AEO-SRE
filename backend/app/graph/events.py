"""Domain event -> graph mapping (the ONLY place that knows how Postgres rows become graph nodes/relationships).

Every builder turns one domain row into one or more `EventSpec`s whose payload is a self-contained *projection plan*:

    {"v": 1,
     "nodes": [{"label", "id", "props", "state", "state_at", "touch"}],     # props: immutable facts; state: mutable,
     "rels":  [{"from": [label, id], "type", "to": [label, id], "props"}],  #   applied only if state_at is not older
     "event": {"type", "occurred_at", "recorded_at", "source", "source_mode", "correlation_id", "props",
               "about": [[label, id]], "emitted_by": [label, id] | None} | None}

The same builders serve (a) the incremental ORM `after_flush` hook (outbox rows are written in the SAME transaction as the
domain mutation, so a rollback leaves nothing behind) and (b) `replay` (rebuilding outbox rows from Postgres history), so a
replayed event has the same deterministic id as the incremental one and is skipped when it already exists.
`Session` here is always the sync SQLAlchemy session (AsyncSession.run_sync hands it over inside greenlet context).
"""
from __future__ import annotations

import uuid
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.graph import model as gm
from app.graph.outbox import EventSpec, insert_outbox_sync
from app.models.changeguard import CanonicalClaim, ChangeCheck, ChangeSet
from app.models.core import Evidence, Incident, Organization, PromptCluster
from app.models.evidence import Evidence as _Evidence  # noqa: F401
from app.models.interventions import (
    Approval,
    Execution,
    Experiment,
    ExperimentOutcome,
    Intervention,
    Observation,
    Reward,
)
from app.models.policy import PolicyDecision, PolicyVersion

log = structlog.get_logger()
OWN_AGENT_ID = "aeo-sre"
MAX_LIST = 50


# ---------------------------------------------------------------------------------------------------------------
# helpers
def _g(obj: Any, name: str) -> Any:
    """Attribute value, or None when it is expired/unloaded (reading it inside a flush hook would need I/O)."""
    st = inspect(obj)
    if name in st.unloaded:
        return None
    v = getattr(obj, name, None)
    return getattr(v, "value", v)


def _str(v: Any) -> str | None:
    return None if v is None else str(v)


def _uuid(v: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(v))
    except (ValueError, TypeError, AttributeError):
        return None


class Plan:
    def __init__(self) -> None:
        self.nodes: list[dict[str, Any]] = []
        self.rels: list[dict[str, Any]] = []
        self.event: dict[str, Any] | None = None

    def node(self, label: str, nid: Any, props: dict[str, Any] | None = None, *, state: dict[str, Any] | None = None,
             state_at: Any = None, touch: bool = False) -> None:
        self.nodes.append({"label": label, "id": str(nid), "props": gm.clean_props(props),
                           "state": gm.clean_props(state), "state_at": gm.iso(state_at), "touch": touch})

    def rel(self, a: tuple[str, Any], rtype: str, b: tuple[str, Any], props: dict[str, Any] | None = None) -> None:
        self.rels.append({"from": [a[0], str(a[1])], "type": rtype, "to": [b[0], str(b[1])],
                          "props": gm.clean_props(props)})

    def emit(self, etype: str, *, occurred_at: Any, recorded_at: Any, source: str, source_mode: str,
             correlation_id: Any, about: list[tuple[str, Any]], emitted_by: tuple[str, Any] | None = None,
             props: dict[str, Any] | None = None) -> None:
        self.event = {
            "type": etype, "occurred_at": gm.iso(occurred_at), "recorded_at": gm.iso(recorded_at), "source": source,
            "source_mode": source_mode, "correlation_id": _str(correlation_id), "props": gm.clean_props(props),
            "about": [[a, str(b)] for a, b in about], "emitted_by": [emitted_by[0], str(emitted_by[1])] if emitted_by else None,
        }

    def dump(self) -> dict[str, Any]:
        return {"v": gm.PLAN_VERSION, "nodes": self.nodes, "rels": self.rels, "event": self.event}


class Ctx:
    """Per-call lookup helper (identity-mapped `session.get`, small caches)."""

    def __init__(self, session: Session):
        self.s = session
        self._fixture: dict[str, bool] = {}
        self.test_env = get_settings().environment == "test"

    def incident_org(self, incident_id: Any) -> str | None:
        inc = self.s.get(Incident, _uuid(incident_id)) if incident_id else None
        return _str(inc.org_id) if inc else None

    def intervention_incident(self, intervention_id: Any) -> uuid.UUID | None:
        iv = self.s.get(Intervention, _uuid(intervention_id)) if intervention_id else None
        return iv.incident_id if iv else None

    def experiment_org(self, experiment_id: Any) -> tuple[str | None, uuid.UUID | None]:
        exp = self.s.get(Experiment, _uuid(experiment_id)) if experiment_id else None
        return (self.incident_org(exp.incident_id), exp.incident_id) if exp else (None, None)

    def is_fixture(self, org_id: Any) -> bool:
        key = str(org_id)
        if key not in self._fixture:
            org = self.s.get(Organization, _uuid(org_id))
            self._fixture[key] = bool(org and "DEV FIXTURE" in (org.name or "").upper())
        return self._fixture[key]

    def mode(self, org_id: Any, *, simulated: bool = False) -> str:
        if simulated:
            return "SIMULATED"
        if self.is_fixture(org_id):
            return "FIXTURE"
        return "TEST" if self.test_env else "LIVE"


def _spec(etype: str, atype: str, aid: Any, version: str, org: Any, plan: Plan, created_at: Any = None) -> EventSpec:
    return EventSpec(etype, atype, str(aid), version, str(org), plan.dump(), created_at)


def _state_hash(*vals: Any) -> str:
    return gm.derived_id("state", *vals)[:16]


# ---------------------------------------------------------------------------------------------------------------
# builders: each returns list[EventSpec] (empty when the row has nothing to project)
def b_organization(c: Ctx, o: Organization) -> list[EventSpec]:
    p = Plan()
    p.node("Organization", o.id, {"name": o.name, "domain": o.domain, "occurred_at": o.created_at})
    return [_spec("organization.upserted", "organization", o.id, "1", o.id, p, o.created_at)]


def b_prompt_cluster(c: Ctx, pc: PromptCluster) -> list[EventSpec]:
    p = Plan()
    p.node("PromptCluster", pc.id, {"topic": pc.topic, "prompt_count": len(pc.prompts or []),
                                    "occurred_at": pc.created_at})
    return [_spec("prompt_cluster.upserted", "prompt_cluster", pc.id, "1", pc.org_id, p, pc.created_at)]


def b_incident(c: Ctx, inc: Incident, *, created: bool) -> list[EventSpec]:
    org = _str(inc.org_id)
    p = Plan()
    state_at = _g(inc, "updated_at") or inc.detected_at
    p.node("Incident", inc.id, {"number": inc.number, "title": (inc.title or "")[:300], "category": inc.category,
                                "severity": inc.severity, "detected_at": inc.detected_at},
           state={"state": _g(inc, "state"), "priority": _g(inc, "priority")}, state_at=state_at)
    if inc.prompt_cluster_id:
        p.rel(("Incident", inc.id), "ABOUT", ("PromptCluster", inc.prompt_cluster_id))
    out = [_spec("incident.changed", "incident", inc.id, f"state:{_g(inc, 'state')}", org, p, state_at)]
    if created:
        e = Plan()
        e.node("Incident", inc.id, {"incident_id": str(inc.id)}, touch=True)
        e.emit("INCIDENT_CREATED", occurred_at=inc.detected_at, recorded_at=inc.created_at, source="SYSTEM",
               source_mode=c.mode(org), correlation_id=inc.id, about=[("Incident", inc.id)],
               props={"category": inc.category, "severity": inc.severity})
        out.append(_spec("incident.created", "incident", inc.id, "1", org, e, inc.created_at))
    return out


def b_intervention(c: Ctx, iv: Intervention) -> list[EventSpec]:
    org = c.incident_org(iv.incident_id)
    if not org:
        return []
    p = Plan()
    state_at = _g(iv, "updated_at") or iv.created_at
    p.node("Intervention", iv.id, {"action": _g(iv, "action"), "title": (iv.title or "")[:300], "risk": _g(iv, "risk"),
                                   "incident_id": str(iv.incident_id), "occurred_at": iv.created_at},
           state={"selected": bool(iv.selected), "selection_basis": _g(iv, "selection_basis")}, state_at=state_at)
    p.rel(("Intervention", iv.id), "ABOUT", ("Incident", iv.incident_id))
    if iv.policy_version_id:
        p.rel(("Intervention", iv.id), "USED", ("PolicyVersion", iv.policy_version_id))
    return [_spec("intervention.changed", "intervention", iv.id, _state_hash(iv.selected, iv.policy_version_id), org, p,
                  state_at)]


def _decision_for_approval(c: Ctx, ap: Approval) -> str | None:
    if not ap.action_digest:
        return None
    with c.s.no_autoflush:
        row = c.s.execute(
            select(ChangeCheck.id).join(ChangeSet, ChangeSet.id == ChangeCheck.change_set_id)
            .where(ChangeSet.intervention_id == ap.intervention_id, ChangeCheck.action_digest == ap.action_digest)
            .order_by(ChangeCheck.created_at.desc()).limit(1)).first()
    return str(row[0]) if row else None


def b_approval(c: Ctx, ap: Approval) -> list[EventSpec]:
    iid = c.intervention_incident(ap.intervention_id)
    org = c.incident_org(iid)
    if not org:
        return []
    status = str(_g(ap, "status") or "pending")
    state_at = _g(ap, "updated_at") or ap.created_at
    p = Plan()
    p.node("Approval", ap.id, {"intervention_id": str(ap.intervention_id), "requested_by": ap.requested_by,
                               "occurred_at": ap.created_at},
           state={"status": status, "decided_by": _g(ap, "decided_by"), "decided_at": _g(ap, "decided_at"),
                  "decided_actor_type": _g(ap, "decided_actor_type"), "action_digest": _g(ap, "action_digest")},
           state_at=state_at)
    p.rel(("Approval", ap.id), "ABOUT", ("Intervention", ap.intervention_id))
    decision = _decision_for_approval(c, ap) if status in ("approved", "modified") else None
    if decision:
        p.rel(("Approval", ap.id), "APPROVES", ("Decision", decision))
    if status in ("approved", "modified", "rejected"):
        human = (_g(ap, "decided_actor_type") or "human") == "human"
        p.emit("APPROVAL_GRANTED" if status != "rejected" else "APPROVAL_REJECTED",
               occurred_at=_g(ap, "decided_at") or state_at, recorded_at=state_at,
               source="HUMAN" if human else "SYSTEM", source_mode=c.mode(org), correlation_id=iid,
               about=[("Approval", ap.id)], props={"status": status, "decided_by": _g(ap, "decided_by")})
    return [_spec("approval.changed", "approval", ap.id, status, org, p, state_at)]


def b_execution(c: Ctx, ex: Execution) -> list[EventSpec]:
    status = str(_g(ex, "status") or "")
    if status not in ("succeeded", "failed"):
        return []
    iid = c.intervention_incident(ex.intervention_id)
    org = c.incident_org(iid)
    if not org:
        return []
    with c.s.no_autoflush:
        exp_ids = [r[0] for r in c.s.execute(select(Experiment.id).where(
            (Experiment.execution_id == ex.id) | (Experiment.intervention_id == ex.intervention_id))).all()]
    about = [("Intervention", ex.intervention_id), *[("Experiment", e) for e in sorted(set(exp_ids), key=str)]]
    when = _g(ex, "executed_at") or _g(ex, "finished_at") or _g(ex, "updated_at") or ex.created_at
    human = bool(_g(ex, "executed_by")) or ex.executor == "manual"
    p = Plan()
    p.emit("CHANGE_EXECUTED" if status == "succeeded" else "CHANGE_EXECUTION_FAILED", occurred_at=when,
           recorded_at=_g(ex, "updated_at") or ex.created_at, source="HUMAN" if human else "SYSTEM",
           source_mode=c.mode(org, simulated=bool(ex.dry_run)), correlation_id=iid, about=about,
           props={"executor": ex.executor, "dry_run": bool(ex.dry_run), "deviation": bool(_g(ex, "deviation")),
                  "reference": (ex.reference or "")[:300], "execution_id": str(ex.id)})
    return [_spec("execution.recorded", "execution", ex.id, status, org, p, when)]


def _experiment_targets(c: Ctx, exp: Experiment, org: str) -> list[str]:
    from app.changeguard.targets import try_normalize

    out: list[str] = []
    key = exp.target_key or ""
    if key.startswith("target:"):
        n = try_normalize(key[len("target:"):])
        if n:
            out.append(n)
    change = exp.proposed_change if isinstance(exp.proposed_change, dict) else {}
    n = try_normalize(change.get("target_url") or (change.get("manual_task") or {}).get("target_url"))
    if n:
        out.append(n)
    if not out:
        with c.s.no_autoflush:
            rows = c.s.execute(select(Evidence.url).where(Evidence.incident_id == exp.incident_id,
                                                         Evidence.type == "owned", Evidence.url.is_not(None))
                               .order_by(Evidence.url).limit(20)).scalars().all()
        out = [n for n in (try_normalize(u) for u in rows) if n]
    return list(dict.fromkeys(out))[:10]


def _timeline_at(exp: Experiment, status: str) -> str | None:
    for e in (exp.timeline or []):
        if isinstance(e, dict) and e.get("to") == status and e.get("at"):
            return e["at"]
    return None


def b_experiment(c: Ctx, exp: Experiment, versions: list[str]) -> list[EventSpec]:
    org = c.incident_org(exp.incident_id)
    if not org:
        return []
    state_at = _g(exp, "updated_at") or exp.created_at
    p = Plan()
    p.node("Experiment", exp.id, {"code": exp.code, "number": exp.number, "action": _g(exp, "selected_action"),
                                  "incident_id": str(exp.incident_id), "intervention_id": str(exp.intervention_id),
                                  "selection_basis": _g(exp, "selection_basis"), "cold_start": exp.cold_start,
                                  "occurred_at": exp.created_at},
           state={"status": _g(exp, "status"), "executed_at": _g(exp, "executed_at"), "executor": exp.executor,
                  "dry_run": bool(exp.dry_run), "execution_reference": (exp.execution_reference or "")[:300],
                  "verification_window_start": _g(exp, "verification_window_start"),
                  "verification_window_end": _g(exp, "verification_window_end"),
                  "evaluated_at": _g(exp, "evaluated_at"), "policy_action": exp.policy_action,
                  "approver": exp.approver, "target_key": exp.target_key},
           state_at=state_at)
    p.rel(("Experiment", exp.id), "TRIGGERED_BY", ("Incident", exp.incident_id))
    p.rel(("Intervention", exp.intervention_id), "EXECUTED_AS", ("Experiment", exp.id))
    if exp.approval_id:
        p.rel(("Approval", exp.approval_id), "EXECUTED_AS", ("Experiment", exp.id))
    with c.s.no_autoflush:
        cs_ids = c.s.execute(select(ChangeSet.id).where(ChangeSet.intervention_id == exp.intervention_id)
                             .order_by(ChangeSet.created_at).limit(MAX_LIST)).scalars().all()
    for cid in cs_ids:
        p.rel(("ChangeSet", cid), "EXECUTED_AS", ("Experiment", exp.id))
    if exp.policy_decision_id:
        p.rel(("PolicyDecision", exp.policy_decision_id), "SELECTED", ("Experiment", exp.id))
    for t in _experiment_targets(c, exp, org):
        p.rel(("Experiment", exp.id), "MEASURES", ("Target", gm.target_node_id(org, t)))
    base = p.dump()
    out: list[EventSpec] = []
    for ver in dict.fromkeys(versions):
        plan = {**base, "event": None}
        if ver in ("approved", "verified"):
            q = Plan()
            q.emit("EXPERIMENT_STARTED" if ver == "approved" else "EXPERIMENT_VERIFIED",
                   occurred_at=_timeline_at(exp, ver) or state_at, recorded_at=state_at, source="SYSTEM",
                   source_mode=c.mode(org, simulated=bool(exp.dry_run)), correlation_id=exp.incident_id,
                   about=[("Experiment", exp.id)], props={"experiment_code": exp.code, "status": ver})
            plan["event"] = q.event
        out.append(EventSpec("experiment.changed", "experiment", str(exp.id), ver, org, plan, state_at))
    return out


def b_observation(c: Ctx, ob: Observation) -> list[EventSpec]:
    org, iid = c.experiment_org(ob.experiment_id)
    if not org:
        return []
    p = Plan()
    p.node("Observation", ob.id, {"experiment_id": str(ob.experiment_id), "source": ob.source,
                                  "source_run_id": ob.source_run_id or None, "observed_at": ob.observed_at,
                                  "metric_keys": sorted((ob.metrics or {}).keys())[:50]})
    p.rel(("Experiment", ob.experiment_id), "OBSERVED", ("Observation", ob.id))
    p.emit("OBSERVATION_RECORDED", occurred_at=ob.observed_at, recorded_at=ob.created_at,
           source="PROFOUND" if (ob.source or "").lower() == "profound" else "SYSTEM", source_mode=c.mode(org),
           correlation_id=iid, about=[("Observation", ob.id), ("Experiment", ob.experiment_id)],
           props={"source": ob.source})
    return [_spec("observation.recorded", "observation", ob.id, "1", org, p, ob.created_at)]


def b_outcome(c: Ctx, oc: ExperimentOutcome) -> list[EventSpec]:
    org, iid = c.experiment_org(oc.experiment_id)
    if not org:
        return []
    p = Plan()
    p.node("Outcome", oc.id, {"experiment_id": str(oc.experiment_id), "outcome": oc.outcome,
                              "observe_outcome": oc.observe_outcome, "reward_total": oc.reward_total,
                              "causal_confidence": oc.causal_confidence, "learning_applied": bool(oc.learning_applied),
                              "observed_at": oc.observed_at, "evaluated_at": oc.evaluated_at})
    p.rel(("Experiment", oc.experiment_id), "PRODUCED", ("Outcome", oc.id))
    with c.s.no_autoflush:
        obs = c.s.execute(select(Observation.id).where(Observation.experiment_id == oc.experiment_id)
                          .order_by(Observation.observed_at).limit(MAX_LIST)).scalars().all()
    for o in obs:
        p.rel(("Outcome", oc.id), "DERIVED_FROM", ("Observation", o))
    p.emit("OUTCOME_MEASURED", occurred_at=oc.evaluated_at or oc.created_at, recorded_at=oc.created_at, source="SYSTEM",
           source_mode=c.mode(org), correlation_id=iid, about=[("Outcome", oc.id), ("Experiment", oc.experiment_id)],
           props={"outcome": oc.outcome, "reward_total": oc.reward_total})
    return [_spec("outcome.measured", "outcome", oc.id, "1", org, p, oc.created_at)]


def b_reward(c: Ctx, rw: Reward) -> list[EventSpec]:
    org, iid = c.experiment_org(rw.experiment_id)
    if not org:
        return []
    p = Plan()
    p.emit("REWARD_CREATED", occurred_at=rw.created_at, recorded_at=rw.created_at, source="SYSTEM",
           source_mode=c.mode(org), correlation_id=iid, about=[("Experiment", rw.experiment_id)],
           props={"reward_total": rw.total, "reward_id": str(rw.id)})
    return [_spec("reward.created", "reward", rw.id, "1", org, p, rw.created_at)]


def b_policy_decision(c: Ctx, pd: PolicyDecision) -> list[EventSpec]:
    org = c.incident_org(pd.incident_id)
    if not org:
        return []
    p = Plan()
    p.node("PolicyDecision", pd.id, {"selected_action": pd.selected_action, "probability": pd.probability,
                                     "selection_basis": pd.selection_basis, "cold_start": bool(pd.cold_start),
                                     "n_related": pd.n_related, "occurred_at": pd.created_at,
                                     "incident_id": str(pd.incident_id)})
    p.rel(("PolicyDecision", pd.id), "USED", ("PolicyVersion", pd.policy_version_id))
    p.rel(("PolicyDecision", pd.id), "ABOUT", ("Incident", pd.incident_id))
    return [_spec("policy_decision.created", "policy_decision", pd.id, "1", org, p, pd.created_at)]


def b_policy_version(c: Ctx, pv: PolicyVersion) -> list[EventSpec]:
    org, iid = (c.experiment_org(pv.source_experiment_id) if pv.source_experiment_id else (None, None))
    p = Plan()
    p.node("PolicyVersion", pv.id, {"version": pv.version, "algorithm": pv.algorithm, "n_updates": pv.n_updates,
                                    "occurred_at": pv.created_at, "immutable": True})
    if pv.parent_id:
        p.rel(("PolicyVersion", pv.id), "DERIVED_FROM", ("PolicyVersion", pv.parent_id))
    if pv.source_experiment_id and org:
        p.rel(("PolicyVersion", pv.id), "DERIVED_FROM", ("Experiment", pv.source_experiment_id))
    if pv.parent_id:  # the initial version is a seed, not an update
        p.emit("POLICY_UPDATED", occurred_at=pv.created_at, recorded_at=pv.created_at, source="SYSTEM",
               source_mode=c.mode(org or gm.GLOBAL_ORG), correlation_id=iid or pv.id, about=[("PolicyVersion", pv.id)],
               props={"version": pv.version, "algorithm": pv.algorithm})
    return [_spec("policy_version.created", "policy_version", pv.id, "1", org or gm.GLOBAL_ORG, p, pv.created_at)]


def b_canonical_claim(c: Ctx, cc: CanonicalClaim) -> list[EventSpec]:
    org = _str(cc.org_id)
    state_at = _g(cc, "updated_at") or cc.created_at
    status = _g(cc, "status")
    ver = _state_hash(status, cc.statement, _g(cc, "valid_from"), _g(cc, "valid_until"), _g(cc, "updated_by"),
                      gm.iso(state_at))
    p = Plan()
    p.node("Claim", cc.id, {"canonical": True, "key": cc.key, "statement": (cc.statement or "")[:500],
                            "occurred_at": cc.created_at},
           state={"status": status, "valid_from": _g(cc, "valid_from"), "valid_until": _g(cc, "valid_until"),
                  "retired_at": _g(cc, "retired_at")}, state_at=state_at)
    p.emit("CANONICAL_CLAIM_CHANGED", occurred_at=state_at, recorded_at=state_at, source="HUMAN",
           source_mode=c.mode(org), correlation_id=cc.id, about=[("Claim", cc.id)],
           props={"key": cc.key, "status": status, "actor": _g(cc, "updated_by") or cc.created_by})
    return [_spec("canonical_claim.changed", "canonical_claim", cc.id, ver, org, p, state_at)]


def b_change_set(c: Ctx, cs: ChangeSet) -> list[EventSpec]:
    org = _str(cs.org_id)
    live_external = cs.source_mode == "LIVE" and cs.origin == "external"
    simulated = cs.source_mode == "SIMULATED"
    iid = c.intervention_incident(cs.intervention_id) if cs.intervention_id else None
    corr = iid or cs.id
    aref = cs.agent_id
    aid = gm.agent_node_id(org, aref)
    mode = c.mode(org, simulated=simulated)
    p = Plan()
    aprops: dict[str, Any] = {"agent_ref": aref, "name": cs.agent_name, "origin": cs.origin, "source_mode": mode}
    if live_external:
        aprops["profound_agent_id"] = aref  # real only for LIVE external agents; never invented otherwise
    p.node("Agent", aid, aprops)
    emitter: tuple[str, Any] = ("Agent", aid)
    proposer = emitter
    if cs.profound_run_id:
        rid = gm.run_node_id(org, aref, cs.profound_run_id)
        rprops: dict[str, Any] = {"run_ref": cs.profound_run_id, "agent_ref": aref, "source_mode": mode}
        if live_external:
            rprops["profound_run_id"] = cs.profound_run_id
        p.node("AgentRun", rid, rprops)
        p.rel(("Agent", aid), "STARTED", ("AgentRun", rid))
        emitter = proposer = ("AgentRun", rid)
    p.node("ChangeSet", cs.id, {"action_type": cs.action_type, "origin": cs.origin, "source_mode": mode,
                                "target_key": cs.target_key or None, "proposal_digest": cs.proposal_digest,
                                "reason": (cs.reason or "")[:500], "risk": cs.risk, "agent_ref": aref,
                                "intervention_id": _str(cs.intervention_id), "occurred_at": cs.created_at,
                                "claim_count": len(cs.proposed_claims or [])})
    p.rel(proposer, "PROPOSED", ("ChangeSet", cs.id))
    if cs.intervention_id:
        p.rel(("ChangeSet", cs.id), "DERIVED_FROM", ("Intervention", cs.intervention_id))
    if cs.target_key:
        tid = gm.target_node_id(org, cs.target_key.removeprefix("target:"))
        p.node("Target", tid, {"key": cs.target_key.removeprefix("target:"), "kind": "page"})
        p.rel(("ChangeSet", cs.id), "MODIFIES", ("Target", tid))
    for text in list(cs.proposed_claims or [])[:MAX_LIST]:
        if not isinstance(text, str) or not text.strip():
            continue
        cid = gm.claim_node_id(org, text)
        p.node("Claim", cid, {"canonical": False, "statement": text[:500]})
        p.rel(("ChangeSet", cs.id), "ALTERS", ("Claim", cid))
    for raw in list(cs.prompt_cluster_ids or [])[:MAX_LIST]:
        u = _uuid(raw)
        pc = c.s.get(PromptCluster, u) if u else None
        if pc is not None and str(pc.org_id) == org:
            p.rel(("ChangeSet", cs.id), "AFFECTS", ("PromptCluster", pc.id))
    if cs.origin == "intervention":
        src = "SYSTEM"
    else:
        src = "PROFOUND" if (live_external and cs.profound_run_id) else "CHANGE_GUARD"
    p.emit("CHANGE_PROPOSED", occurred_at=cs.created_at, recorded_at=cs.created_at, source=src, source_mode=mode,
           correlation_id=corr, about=[("ChangeSet", cs.id)], emitted_by=emitter,
           props={"action_type": cs.action_type, "agent_ref": aref, "origin": cs.origin})
    return [_spec("change_set.created", "change_set", cs.id, "1", org, p, cs.created_at)]


_CONFLICT_DECISIONS = ("BLOCK", "DELAY", "REQUIRE_REVIEW", "MERGE")


def b_change_check(c: Ctx, ck: ChangeCheck) -> list[EventSpec]:
    org = _str(ck.org_id)
    cs = c.s.get(ChangeSet, ck.change_set_id)
    if cs is None:
        return []
    iid = c.intervention_incident(cs.intervention_id) if cs.intervention_id else None
    corr = iid or cs.id
    mode = c.mode(org, simulated=cs.source_mode == "SIMULATED")
    created = ck.created_at or ck.evaluated_at
    with c.s.no_autoflush:
        prev = c.s.execute(select(ChangeCheck.id).where(
            ChangeCheck.change_set_id == ck.change_set_id, ChangeCheck.id != ck.id,
            ChangeCheck.created_at <= created).order_by(ChangeCheck.created_at.desc(), ChangeCheck.id.desc())
            .limit(1)).first()
    findings = [f for f in (ck.findings or []) if isinstance(f, dict)]
    conflicts = [(i, f) for i, f in enumerate(findings)
                 if f.get("decision") in _CONFLICT_DECISIONS and f.get("severity") != "info"]
    cids = {i: gm.conflict_node_id(ck.id, i, str(f.get("type"))) for i, f in conflicts}

    d = Plan()
    d.node("Decision", ck.id, {"decision": ck.decision, "eligible_after": ck.eligible_after, "guard_version": ck.guard_version,
                               "action_digest": ck.action_digest, "semantic_check": ck.semantic_check,
                               "change_set_id": str(ck.change_set_id), "intervention_id": _str(cs.intervention_id),
                               "finding_count": len(findings), "conflict_count": len(conflicts), "source_mode": mode,
                               "occurred_at": created})
    d.node("ChangeSet", cs.id, {}, state={"status": ck.decision, "last_decision_id": str(ck.id)}, state_at=created,
           touch=True)
    d.rel(("Decision", ck.id), "DECIDES_ON", ("ChangeSet", cs.id))
    for i in cids:
        d.rel(("Decision", ck.id), "BASED_ON", ("Conflict", cids[i]))
    if prev:
        d.rel(("Decision", prev[0]), "FOLLOWED_BY", ("Decision", ck.id))
    d.emit("DECISION_CREATED", occurred_at=created, recorded_at=created, source="CHANGE_GUARD", source_mode=mode,
           correlation_id=corr, about=[("Decision", ck.id), ("ChangeSet", cs.id)],
           props={"decision": ck.decision, "conflict_count": len(conflicts), "guard_version": ck.guard_version})
    out = [_spec("change_check.decided", "change_check", ck.id, "1", org, d, created)]
    if not conflicts:
        return out

    k = Plan()
    tid = gm.target_node_id(org, cs.target_key.removeprefix("target:")) if cs.target_key else None
    for i, f in conflicts:
        cid = cids[i]
        refs = f.get("references") if isinstance(f.get("references"), dict) else {}
        k.node("Conflict", cid, {"conflict_type": f.get("type"), "check": f.get("check"), "severity": f.get("severity"),
                                 "decision": f.get("decision"), "reason": (f.get("reason") or "")[:500],
                                 "check_id": str(ck.id), "change_set_id": str(cs.id), "status": "open",
                                 "occurred_at": created})
        if tid:
            k.rel(("Conflict", cid), "ABOUT", ("Target", tid))
        k.rel(("Conflict", cid), "INVOLVES", ("ChangeSet", cs.id))
        eid = _uuid(refs.get("experiment_id"))
        if eid:
            k.rel(("Conflict", cid), "PROTECTS", ("Experiment", eid))
        ocs = _uuid(refs.get("change_set_id"))
        if ocs and ocs != cs.id:
            k.rel(("Conflict", cid), "INVOLVES", ("ChangeSet", ocs))
            k.rel(("ChangeSet", cs.id), "CONFLICTS_WITH", ("ChangeSet", ocs), {"conflict_id": cid})
        oiv = _uuid(refs.get("intervention_id"))
        if oiv:
            k.rel(("Conflict", cid), "INVOLVES", ("Intervention", oiv))
        ccl = _uuid(refs.get("canonical_claim_id"))
        if ccl:
            k.rel(("Conflict", cid), "INVOLVES", ("Claim", ccl))
        det = f.get("details") if isinstance(f.get("details"), dict) else {}
        if det.get("proposed_claim"):
            k.rel(("Conflict", cid), "INVOLVES", ("Claim", gm.claim_node_id(org, det["proposed_claim"])))
    k.emit("CONFLICT_DETECTED", occurred_at=created, recorded_at=created, source="CHANGE_GUARD", source_mode=mode,
           correlation_id=corr, about=[("ChangeSet", cs.id), *[("Conflict", cids[i]) for i in cids]],
           props={"conflict_types": sorted({str(f.get("type")) for _, f in conflicts}), "check_id": str(ck.id)})
    out.append(_spec("change_check.conflicts", "change_check", ck.id, "conflicts", org, k, created))
    return out


# ---------------------------------------------------------------------------------------------------------------
# incremental hook (ORM after_flush)
def _changed(obj: Any, *names: str) -> bool:
    st = inspect(obj)
    return any(st.attrs[n].history.has_changes() for n in names if n in st.attrs)


def _new_timeline_entries(exp: Experiment) -> list[dict[str, Any]]:
    hist = inspect(exp).attrs.timeline.history
    if not hist.has_changes() or not hist.added:
        return []
    old = hist.deleted[0] if hist.deleted else None
    new = list(hist.added[0] or [])
    return [e for e in (new[len(old):] if old is not None else new) if isinstance(e, dict)]


def specs_for_flush(session: Session) -> list[EventSpec]:
    c = Ctx(session)
    out: list[EventSpec] = []

    def run(fn: Callable[[], list[EventSpec]], what: str) -> None:
        try:
            out.extend(fn())
        except Exception as exc:  # noqa: BLE001 - the graph must never break a domain write; replay recovers it
            log.error("graph.outbox_build_failed", what=what, error=f"{type(exc).__name__}: {exc}")

    for obj in list(session.new):
        if isinstance(obj, Organization):
            run(lambda o=obj: b_organization(c, o), "organization")
        elif isinstance(obj, PromptCluster):
            run(lambda o=obj: b_prompt_cluster(c, o), "prompt_cluster")
        elif isinstance(obj, Incident):
            run(lambda o=obj: b_incident(c, o, created=True), "incident")
        elif isinstance(obj, Intervention):
            run(lambda o=obj: b_intervention(c, o), "intervention")
        elif isinstance(obj, Approval):
            run(lambda o=obj: b_approval(c, o), "approval")
        elif isinstance(obj, Execution):
            run(lambda o=obj: b_execution(c, o), "execution")
        elif isinstance(obj, Experiment):
            run(lambda o=obj: b_experiment(c, o, [str(_g(o, "status"))] + [e.get("to") for e in (o.timeline or [])
                                                                          if isinstance(e, dict) and e.get("to")]),
                "experiment")
        elif isinstance(obj, Observation):
            run(lambda o=obj: b_observation(c, o), "observation")
        elif isinstance(obj, ExperimentOutcome):
            run(lambda o=obj: b_outcome(c, o), "outcome")
        elif isinstance(obj, Reward):
            run(lambda o=obj: b_reward(c, o), "reward")
        elif isinstance(obj, PolicyDecision):
            run(lambda o=obj: b_policy_decision(c, o), "policy_decision")
        elif isinstance(obj, PolicyVersion):
            run(lambda o=obj: b_policy_version(c, o), "policy_version")
        elif isinstance(obj, ChangeSet):
            run(lambda o=obj: b_change_set(c, o), "change_set")
        elif isinstance(obj, ChangeCheck):
            run(lambda o=obj: b_change_check(c, o), "change_check")
        elif isinstance(obj, CanonicalClaim):
            run(lambda o=obj: b_canonical_claim(c, o), "canonical_claim")
    for obj in list(session.dirty):
        if obj in session.new:
            continue
        if isinstance(obj, Incident) and _changed(obj, "state"):
            run(lambda o=obj: b_incident(c, o, created=False), "incident")
        elif isinstance(obj, Intervention) and _changed(obj, "selected", "policy_version_id"):
            run(lambda o=obj: b_intervention(c, o), "intervention")
        elif isinstance(obj, Approval) and _changed(obj, "status"):
            run(lambda o=obj: b_approval(c, o), "approval")
        elif isinstance(obj, Execution) and _changed(obj, "status"):
            run(lambda o=obj: b_execution(c, o), "execution")
        elif isinstance(obj, Experiment):
            entries = _new_timeline_entries(obj)
            if entries:
                run(lambda o=obj, e=entries: b_experiment(c, o, [x.get("to") for x in e if x.get("to")]), "experiment")
        elif isinstance(obj, CanonicalClaim) and _changed(obj, "status", "statement", "valid_from", "valid_until"):
            run(lambda o=obj: b_canonical_claim(c, o), "canonical_claim")
    return out


def on_after_flush(session: Session) -> None:
    """ORM hook body: same transaction as the domain flush. A build failure is logged, never raised."""
    if not get_settings().graph_outbox_enabled:
        return
    if not (session.new or session.dirty):
        return
    specs = specs_for_flush(session)
    if not specs:
        return
    conn = session.connection()
    for s in specs:
        insert_outbox_sync(conn, s)


# ---------------------------------------------------------------------------------------------------------------
# replay: the same builders over authoritative Postgres history
def history_specs(session: Session, organization_id: Any = None) -> Iterator[EventSpec]:
    """Yield every EventSpec the incremental hook would have produced (or the closest reproducible equivalent: one
    state row per mutable aggregate carrying its current state, one row per recorded experiment transition)."""
    c = Ctx(session)
    org = _uuid(organization_id) if organization_id else None

    def rows(model: Any, *where: Any, order: Any = None) -> list[Any]:
        stmt = select(model).where(*where) if where else select(model)
        return list(session.execute(stmt.order_by(order if order is not None else model.created_at)).scalars().all())

    def safe(fn: Callable[[], list[EventSpec]], what: str) -> list[EventSpec]:
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001
            log.error("graph.replay_build_failed", what=what, error=f"{type(exc).__name__}: {exc}")
            return []

    for o in rows(Organization, *([Organization.id == org] if org else [])):
        yield from safe(lambda o=o: b_organization(c, o), "organization")
    inc_org = [Incident.org_id == org] if org else []
    for o in rows(PromptCluster, *([PromptCluster.org_id == org] if org else [])):
        yield from safe(lambda o=o: b_prompt_cluster(c, o), "prompt_cluster")
    incidents = rows(Incident, *inc_org)
    inc_ids = {i.id for i in incidents}
    for o in incidents:
        specs = safe(lambda o=o: b_incident(c, o, created=True), "incident")
        yield from specs
    for iv in rows(Intervention):
        if (not org) or iv.incident_id in inc_ids:
            yield from safe(lambda o=iv: b_intervention(c, o), "intervention")
    ivs = {iv.id for iv in rows(Intervention) if (not org) or iv.incident_id in inc_ids}
    for o in rows(PolicyVersion):
        if org and o.source_experiment_id is None and False:  # versions are global; always projected
            continue
        yield from safe(lambda o=o: b_policy_version(c, o), "policy_version")
    for o in rows(PolicyDecision):
        if (not org) or o.incident_id in inc_ids:
            yield from safe(lambda o=o: b_policy_decision(c, o), "policy_decision")
    for o in rows(CanonicalClaim, *([CanonicalClaim.org_id == org] if org else [])):
        yield from safe(lambda o=o: b_canonical_claim(c, o), "canonical_claim")
    for o in rows(ChangeSet, *([ChangeSet.org_id == org] if org else [])):
        yield from safe(lambda o=o: b_change_set(c, o), "change_set")
    for o in rows(ChangeCheck, *([ChangeCheck.org_id == org] if org else [])):
        yield from safe(lambda o=o: b_change_check(c, o), "change_check")
    for o in rows(Approval):
        if (not org) or o.intervention_id in ivs:
            yield from safe(lambda o=o: b_approval(c, o), "approval")
    for o in rows(Execution):
        if (not org) or o.intervention_id in ivs:
            yield from safe(lambda o=o: b_execution(c, o), "execution")
    exps = [e for e in rows(Experiment) if (not org) or e.incident_id in inc_ids]
    exp_ids = {e.id for e in exps}
    for e in exps:
        versions = ["proposed", *[x.get("to") for x in (e.timeline or []) if isinstance(x, dict) and x.get("to")],
                    str(_g(e, "status"))]
        yield from safe(lambda e=e, v=versions: b_experiment(c, e, v), "experiment")
    for o in rows(Observation):
        if (not org) or o.experiment_id in exp_ids:
            yield from safe(lambda o=o: b_observation(c, o), "observation")
    for o in rows(ExperimentOutcome):
        if (not org) or o.experiment_id in exp_ids:
            yield from safe(lambda o=o: b_outcome(c, o), "outcome")
    for o in rows(Reward):
        if (not org) or o.experiment_id in exp_ids:
            yield from safe(lambda o=o: b_reward(c, o), "reward")
