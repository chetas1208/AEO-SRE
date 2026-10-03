"""GET /api/experiments[/{id}] expose what B4 persisted: spec, declared metrics, outcome, override, verification.
Everything is read from persisted rows; absent data is null (never defaulted)."""
import pytest
from app.domain.enums import IncidentState as S
from app.models.interventions import ExperimentOutcome

from tests import factories as f

pytestmark = pytest.mark.asyncio

SPEC = {
    "if_action": "update_existing_page", "because_root_cause": "stale pricing page",
    "then_metric": "visibility", "direction": "increase", "window_hours": 216, "delay_hours": 48,
    "primary_metric": "visibility", "secondary_metrics": ["citation_share"], "declared_at": "2026-10-01T10:00:00Z",
    "statement": "IF we update the page BECAUSE stale THEN visibility SHOULD increase AFTER 48h", "observe": False,
    "spec_hash": "abc123",
}


async def _setup(session, org, **kw):
    inc = await f.make_incident(session, org, state=S.AWAITING_VERIFICATION.value)
    iv = await f.make_intervention(session, inc)
    exp = await f.make_experiment(session, inc, iv, None, executed_at=f.utc(3), verification_window_start=f.utc(1),
                                  verification_window_end=f.utc(-6), **kw)
    return inc, iv, exp


async def test_detail_exposes_spec_override_verification(app_client, session, org):
    _, _, exp = await _setup(session, org, spec=SPEC, policy_action="observe", override_reason="ops prefers page edit",
                             override_by="alice")
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["spec"]["if_action"] == "update_existing_page" and d["spec"]["because_root_cause"] == "stale pricing page"
    assert d["spec"]["then_metric"] == "visibility" and d["spec"]["window_hours"] == 216
    assert d["declared_metrics"] == {"primary": "visibility", "secondary": ["citation_share"]}
    assert d["override"]["overridden"] is True and d["override"]["policy_action"] == "observe"
    assert d["override"]["executed_action"] == str(getattr(exp.selected_action, "value", exp.selected_action)) and d["override"]["reason"] == "ops prefers page edit"
    v = d["verification"]
    assert v["eligible_at"] and v["window_end"] and v["is_open"] is True and v["delay_hours"] == 48
    assert v["rules"], "measurement rules are served, not hardcoded in the client"
    assert d["outcome"] is None and d["display_status"] == "Awaiting Measurement"


async def test_unknown_fields_are_null_not_defaulted(app_client, session, org):
    _, _, exp = await _setup(session, org)
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["spec"] is None and d["declared_metrics"] is None and d["outcome"] is None
    assert d["override"]["overridden"] is False and d["override"]["reason"] is None


async def test_inconclusive_outcome_has_label_and_reason(app_client, session, org):
    _, _, exp = await _setup(session, org, spec=SPEC, status="verified", after_metrics={"visibility": 0.4})
    session.add(ExperimentOutcome(
        experiment_id=exp.id, outcome="inconclusive", components={}, confounders=[{"kind": "overlapping_intervention", "hard": True}],
        causal_confidence="low", learning_applied=False,
        methodology={"reason": "unattributable: overlapping_intervention", "causal": {"statement": "no claim"}}))
    await session.commit()
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["display_status"] == "Inconclusive" and d["awaiting_reward"] is False
    o = d["outcome"]
    assert o["label"] == "inconclusive" and o["inconclusive_reason"] == "unattributable: overlapping_intervention"
    assert o["reward_total"] is None and o["learning_applied"] is False and o["confounders"][0]["kind"] == "overlapping_intervention"
    assert o["causal_confidence"] == "low" and o["causal_statement"] == "no claim"
    row = next(r for r in (await app_client.get("/api/experiments")).json()["items"] if r["id"] == str(exp.id))
    assert row["outcome"] == "inconclusive" and row["display_status"] == "Inconclusive"
    assert row["inconclusive_reason"] == "unattributable: overlapping_intervention"


async def test_favorable_outcome_in_detail_and_list(app_client, session, org):
    _, _, exp = await _setup(session, org, spec=SPEC, status="verified", after_metrics={"visibility": 0.5})
    session.add(ExperimentOutcome(experiment_id=exp.id, outcome="favorable", reward_total=0.21,
                                  components={"visibility": 0.1}, confounders=[], causal_confidence="medium",
                                  learning_applied=True, methodology={"causal": {"statement": "association"}}))
    await session.commit()
    d = (await app_client.get(f"/api/experiments/{exp.id}")).json()
    assert d["outcome"]["label"] == "favorable" and d["outcome"]["reward_total"] == 0.21
    assert d["outcome"]["components"] == {"visibility": 0.1} and d["outcome"]["inconclusive_reason"] is None
    row = (await app_client.get("/api/experiments")).json()["items"][0]
    assert row["outcome"] == "favorable" and row["display_status"] == "Verified"
