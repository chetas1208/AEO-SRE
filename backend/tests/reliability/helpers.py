"""Shared builders for the reliability tests. All data here is RECORDED/TEST ONLY (never shown as live)."""
from __future__ import annotations

import uuid
from datetime import timedelta

from app.domain.enums import ApprovalStatus, ExperimentStatus, IncidentState
from app.experiments import ledger
from app.interventions.changes import FileChange, ManualTask, ProposedChange, wrap_section
from app.models.interventions import Observation
from app.services import approvals as appr

from tests import factories as f

CHANGE = ProposedChange(
    title="Add SAML section", target_url="https://testco.example/enterprise/security", summary="add section",
    files=[FileChange(path="content/security.md", old_content="# Security\n",
                      new_content=wrap_section("incident-1", "## SAML SSO\n\nSAML SSO is supported."),
                      patch_mode="upsert_section", section_id="incident-1")]).to_json()
OBSERVE = ProposedChange(kind="observe", title="Monitor", summary="watch",
                         manual_task=ManualTask(kind="observe", title="Monitor", checklist=["re-measure"])).to_json()
MODIFIED = ProposedChange(
    title="Add SAML section (human edit)", target_url="https://testco.example/enterprise/security",
    summary="human-edited", files=[FileChange(
        path="content/security.md", old_content="# Security\n",
        new_content=wrap_section("incident-1", "## SAML SSO\n\nSAML 2.0 and OIDC SSO are supported (human edit)."),
        patch_mode="upsert_section", section_id="incident-1")]).to_json()


def ctx_json():
    from app.policy import encode_context
    from app.policy.features import FEATURE_NAMES

    return {"names": list(FEATURE_NAMES), "values": [float(v) for v in encode_context(visibility_delta=-0.4)]}


async def proposed_intervention(session, org, *, action="update_existing_page", change=CHANGE,
                                state=IncidentState.AWAITING_APPROVAL):
    """Incident + selected intervention + proposed experiment (as `pipeline.propose` leaves them). No approval yet."""
    inc = await f.make_incident(session, org, state=state)
    iv = await f.make_intervention(session, inc, action=action, proposed_change=change, selected=True)
    pv = await f.make_policy_version(session)
    exp = await ledger.open_experiment(
        session, iv, {"probability": 0.6, "action": iv.action, "scores": [], "selection_basis": "cold_start_prior",
                      "cold_start": True, "policy_version_id": pv.id},
        inc, {"visibility": 37.0}, {"evidence": [{"id": "e1", "hash": "abc"}]}, ctx_json(), now=f.NOW)
    await session.commit()
    return inc, iv, exp


async def approve_and_package(session, org, **kw):
    """Human approval (service layer) + manual package issued; still NOT recorded as executed."""
    from app.interventions.manual import prepare_manual_package

    inc, iv, exp = await proposed_intervention(session, org, state=IncidentState.APPROVED, **kw)
    ap = await appr.request_approval(session, iv)
    await appr.decide_approval(session, ap, ApprovalStatus.APPROVED, "alice", "ok")
    await ledger.attach_approval(session, exp, ap)
    await session.commit()
    await prepare_manual_package(session, iv.id)
    await session.commit()
    return inc, iv, exp


async def executed_experiment(session, org, *, executed_at=None, delay_hours=48, dry_run=False,
                              status=ExperimentStatus.AWAITING_VERIFICATION, **kw):
    """An experiment that is already awaiting verification (state built directly; the paths to it are tested in M8)."""
    executed_at = executed_at or f.NOW
    start = executed_at + timedelta(hours=delay_hours)
    inc = await f.make_incident(session, org, state=IncidentState.AWAITING_VERIFICATION)
    iv = await f.make_intervention(session, inc, proposed_change=CHANGE, selected=True)
    pv = await f.make_policy_version(session)
    exp = await f.make_experiment(
        session, inc, iv, pv, status=status, executed_at=executed_at, verification_window_start=start,
        verification_window_end=start + timedelta(days=7), before_metrics={"visibility": 37.0},
        dry_run=dry_run, context_vector=kw.pop("context_vector", ctx_json()), **kw)
    return inc, iv, exp


async def add_obs(session, exp, observed_at, metrics=None, source="profound"):
    exp_id = exp if isinstance(exp, uuid.UUID) else exp.id
    session.add(Observation(experiment_id=exp_id, metrics=metrics or {"visibility": 52.0}, observed_at=observed_at,
                            source=source))
    await session.commit()
