"""Read-only database integrity report for operators. Never repairs anything.

    cd backend && .venv/bin/python -m app.devtools.audit_db

Exit code 1 when any violation is found, 0 when clean. ``violations`` maps each check to the offending ids (capped).
"""

from __future__ import annotations

import asyncio
import json

from sqlalchemy import func, select

from app.core.db import get_sessionmaker
from app.domain.enums import ExperimentStatus, HypothesisStatus
from app.incidents.state_machine import TERMINAL_STATES
from app.models.core import AuditEvent, Incident, Signal
from app.models.evidence import Evidence, EvidenceEdge, EvidenceNode, Hypothesis
from app.models.interventions import Approval, Experiment, Intervention, Observation, Reward
from app.models.policy import PolicyDecision, PolicyVersion

CAP = 20
_TERMINAL = {s.value for s in TERMINAL_STATES}
_POST_EXEC = {ExperimentStatus.EXECUTED, ExperimentStatus.AWAITING_VERIFICATION, ExperimentStatus.VERIFIED,
              ExperimentStatus.REWARDED}


def _aware(dt):
    from datetime import UTC

    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


async def _all(session, stmt):
    return (await session.execute(stmt)).all()


async def run() -> dict:
    async with get_sessionmaker()() as session:
        counts = {}
        for label, model in (
            ("signals", Signal), ("incidents", Incident), ("evidence", Evidence), ("evidence_edges", EvidenceEdge),
            ("hypotheses", Hypothesis), ("interventions", Intervention), ("approvals", Approval),
            ("experiments", Experiment), ("observations", Observation), ("rewards", Reward),
            ("policy_versions", PolicyVersion), ("policy_decisions", PolicyDecision), ("audit_events", AuditEvent),
        ):
            counts[label] = await session.scalar(select(func.count()).select_from(model))

        v: dict[str, list[str]] = {}

        incident_ids = {r[0] for r in await _all(session, select(Incident.id))}
        evidence_by_inc: dict = {}
        for eid, iid in await _all(session, select(Evidence.id, Evidence.incident_id)):
            evidence_by_inc.setdefault(iid, set()).add(eid)
        node_ids = {r[0]: r[1] for r in await _all(session, select(EvidenceNode.id, EvidenceNode.incident_id))}
        hyp_rows = await _all(session, select(Hypothesis.id, Hypothesis.incident_id, Hypothesis.status,
                                              Hypothesis.evidence_ids))
        hyp_ids = {h[0]: h[1] for h in hyp_rows}

        v["orphan_evidence"] = [str(e) for i, es in evidence_by_inc.items() if i not in incident_ids for e in es]
        all_evidence = {e: i for i, es in evidence_by_inc.items() for e in es}
        orphan_edges = []
        for eid, iid, src, dst in await _all(session, select(EvidenceEdge.id, EvidenceEdge.incident_id,
                                                              EvidenceEdge.src_id, EvidenceEdge.dst_id)):
            def ok(x, iid=iid):
                return (all_evidence.get(x) == iid or node_ids.get(x) == iid or hyp_ids.get(x) == iid or x == iid)

            if iid not in incident_ids or not ok(src) or not ok(dst):
                orphan_edges.append(str(eid))
        v["orphan_edges"] = orphan_edges

        bad_conf = []
        for hid, iid, status, ev_ids in hyp_rows:
            if str(status) != HypothesisStatus.CONFIRMED.value:
                continue
            ids = []
            for x in ev_ids or []:
                try:
                    import uuid as _u

                    ids.append(_u.UUID(str(x)))
                except ValueError:
                    ids.append(None)
            if not ids or any(x is None or all_evidence.get(x) != iid for x in ids):
                bad_conf.append(str(hid))
        v["confirmed_hypothesis_without_valid_evidence"] = bad_conf

        exps = (await session.execute(select(Experiment))).scalars().all()
        exp_by_id = {e.id: e for e in exps}
        v["experiment_without_baseline"] = [str(e.id) for e in exps if not e.before_metrics]
        v["experiment_activated_without_approval"] = [
            str(e.id) for e in exps
            if ExperimentStatus(e.status) not in (ExperimentStatus.PROPOSED, ExperimentStatus.REJECTED,
                                                  ExperimentStatus.FAILED)
            and str(getattr(e.selected_action, "value", e.selected_action)) != "observe" and e.approval_id is None
        ]
        v["post_execution_experiment_without_executed_at"] = [
            str(e.id) for e in exps if ExperimentStatus(e.status) in _POST_EXEC and e.executed_at is None
        ]

        reward_exps = {r[0] for r in await _all(session, select(Reward.experiment_id))}
        obs_rows = await _all(session, select(Observation.id, Observation.experiment_id, Observation.source,
                                              Observation.source_run_id, Observation.observed_at))
        obs_exps = {o[1] for o in obs_rows}
        v["reward_without_observation"] = [str(x) for x in reward_exps if x not in obs_exps]
        v["policy_update_without_reward"] = [
            str(pid) for pid, src in await _all(session, select(PolicyVersion.id, PolicyVersion.source_experiment_id))
            if src is not None and src not in reward_exps
        ]
        v["rewarded_experiment_without_reward"] = [
            str(e.id) for e in exps if ExperimentStatus(e.status) == ExperimentStatus.REWARDED
            and e.id not in reward_exps and not e.dry_run
        ]

        seen: dict = {}
        dup_obs = []
        for oid, eid, src, run_id, at in obs_rows:
            k = (eid, src, run_id, at)
            if k in seen:
                dup_obs.append(str(oid))
            seen[k] = oid
        v["duplicate_source_observations"] = dup_obs

        before_exec = []
        for oid, eid, _s, _r, at in obs_rows:
            e = exp_by_id.get(eid)
            if e is not None and (e.executed_at is None or _aware(at) <= _aware(e.executed_at)):
                before_exec.append(str(oid))
        v["observation_not_after_execution"] = before_exec

        fp: dict = {}
        dup_fp = []
        for iid, f, st in await _all(session, select(Incident.id, Incident.fingerprint, Incident.state)):
            if f and st not in _TERMINAL:
                if f in fp:
                    dup_fp.append(str(iid))
                fp[f] = iid
        v["duplicate_open_incident_fingerprints"] = dup_fp

        idem: dict = {}
        dup_sig = []
        for sid, org, src, key in await _all(session, select(Signal.id, Signal.org_id, Signal.source,
                                                              Signal.idempotency_key)):
            if key:
                if (org, src, key) in idem:
                    dup_sig.append(str(sid))
                idem[(org, src, key)] = sid
        v["duplicate_signal_idempotency_keys"] = dup_sig

        violations = {k: ids[:CAP] for k, ids in v.items() if ids}
        return {
            "counts": counts,
            "checks": sorted(v),
            "violations": violations,
            "violation_counts": {k: len(ids) for k, ids in v.items() if ids},
            "clean": not violations,
            "note": "Read-only; nothing is repaired. Exit code 1 if any violation.",
        }


def main() -> None:
    report = asyncio.run(run())
    print(json.dumps(report, indent=2, default=str))
    raise SystemExit(0 if report["clean"] else 1)


if __name__ == "__main__":
    main()
