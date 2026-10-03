"""Intervention proposal tests (TEST-ONLY fixtures; evidence text below is synthetic and labeled as such)."""
from __future__ import annotations

from typing import Any

import pytest
from app.domain.enums import ActionType, Risk, SelectionBasis
from app.interventions import propose_interventions
from app.interventions.changes import (
    FileChange,
    ProposedChange,
    apply_change,
    build_diff,
    validate_path,
    wrap_section,
)
from app.interventions.facts import EvidenceItem, build_fact_base, ungrounded_tokens
from app.interventions.llm import LLMUnavailable
from app.models.interventions import Approval, Intervention
from app.policy import ActionScore, Decision
from sqlalchemy import select

from tests import factories

SAML = "TestCo supports SAML 2.0 single sign-on on the Enterprise plan."
SCIM = "Automatic user provisioning is available through SCIM 2.0 for Enterprise customers."
THIRD = "TestCo does not offer single sign-on according to this directory listing."


def decision(selected: ActionType = ActionType.UPDATE_EXISTING_PAGE, basis=SelectionBasis.COLD_START_PRIOR) -> Decision:
    probs = {a: 0.05 for a in ActionType}
    probs[selected] = 0.7
    scores = [ActionScore(action=a, mean=probs[a], uncertainty=0.1, ucb=probs[a] + 0.1, probability=probs[a])
              for a in ActionType]
    return Decision(action=selected, probability=0.7, scores=scores, selection_basis=basis, cold_start=True,
                    policy_version="v0.0.1")


class FakeLLM:
    def __init__(self, payload: dict | Exception) -> None:
        self.payload, self.calls = payload, 0

    async def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


async def seed(session, *, with_owned=True, with_third=False, hyp_status="proposed", stale_only=False):
    org = await factories.make_org(session, name="TestCo", domain="testco.example")
    cluster = await factories.make_prompt_cluster(session, org, topic="SAML single sign-on")
    inc = await factories.make_incident(session, org, title="Lost SSO citations", number=12,
                                        prompt_cluster_id=cluster.id)
    ev = []
    if with_owned:
        ev.append(await factories.make_evidence(
            session, inc, type="owned", status="stale" if stale_only else "live", url="https://testco.example/enterprise/security",
            title="Security page", excerpt=f"{SAML} {SCIM}"))
    if with_third:
        ev.append(await factories.make_evidence(
            session, inc, type="external", url="https://directory.example/testco", title="Directory", excerpt=THIRD))
    ev.append(await factories.make_evidence(session, inc, type="profound", title="Visibility metric",
                                            url=None, excerpt="visibility fell 24 points"))
    hyp = await factories.make_hypothesis(session, inc, evidence_ids=[str(e.id) for e in ev],
                                          title="Owned page lacks explicit SAML content", status=hyp_status)
    return org, inc, ev, hyp


async def test_proposes_candidate_for_every_action(session):
    _, inc, _, hyp = await seed(session, with_third=True)
    rows = await propose_interventions(session, inc, decision(), use_default_llm=False)
    await session.commit()
    assert {r.action for r in rows} == set(ActionType) and len(rows) == len(ActionType)
    sel = [r for r in rows if r.selected]
    assert len(sel) == 1 and sel[0].action is ActionType.UPDATE_EXISTING_PAGE
    assert sel[0].score == pytest.approx(0.7) and sel[0].selection_basis is SelectionBasis.COLD_START_PRIOR
    assert rows[0].action is ActionType.UPDATE_EXISTING_PAGE  # ordered by score
    assert all(r.hypothesis_id == hyp.id for r in rows) and all(isinstance(r.risk, Risk) for r in rows)
    assert "not yet confirmed" in sel[0].rationale and "Owned page lacks explicit SAML content" in sel[0].rationale
    assert "cold_start_prior" in sel[0].rationale
    stored = (await session.execute(select(Intervention).where(Intervention.incident_id == inc.id))).scalars().all()
    assert len(stored) == len(ActionType)


async def test_update_page_template_has_capability_heading_faq_and_only_cited_facts(session):
    _, inc, ev, _ = await seed(session)
    rows = await propose_interventions(session, inc, decision(), use_default_llm=False)
    row = next(r for r in rows if r.action is ActionType.UPDATE_EXISTING_PAGE)
    pc = ProposedChange.model_validate(row.proposed_change)
    assert pc.generated_by == "template" and pc.target_url == "https://testco.example/enterprise/security"
    fc = pc.files[0]
    assert fc.path == "enterprise/security.md" and fc.patch_mode == "upsert_section"
    assert "## SAML single sign-on" in fc.new_content and "Frequently asked questions" in fc.new_content
    assert "<!-- aeo-sre:begin" in fc.new_content and SAML in fc.new_content
    assert "Does TestCo support SAML single sign-on?" in fc.new_content
    corpus = SAML + SCIM + " TestCo SAML single sign-on testco.example"
    visible = "\n".join(ln for ln in fc.new_content.splitlines() if not ln.startswith("<!--"))
    assert ungrounded_tokens(visible, corpus) == []
    assert pc.diff and pc.diff.startswith("diff --git a/enterprise/security.md")
    assert str(ev[0].id) in "".join(pc.fact_ids)
    assert row.risk is Risk.MEDIUM  # low base action bumped: root cause still only proposed


async def test_confirmed_root_cause_keeps_low_risk(session):
    _, inc, _, _ = await seed(session, hyp_status="confirmed")
    rows = await propose_interventions(session, inc, decision(), use_default_llm=False)
    assert next(r for r in rows if r.selected).risk is Risk.LOW


async def test_no_evidence_means_no_patch_with_reason_never_invented(session):
    _, inc, _, _ = await seed(session, with_owned=False)
    inc_id = inc.id
    await propose_interventions(session, inc, decision(), use_default_llm=False)
    await session.commit()
    session.expire_all()
    rows = (await session.execute(select(Intervention).where(Intervention.incident_id == inc_id))).scalars().all()
    by = {r.action: r for r in rows}
    for a in (ActionType.UPDATE_EXISTING_PAGE, ActionType.CREATE_FAQ, ActionType.CREATE_CANONICAL_PAGE,
              ActionType.CREATE_COMPARISON_CONTENT, ActionType.STRUCTURED_DATA, ActionType.PUBLISHER_OUTREACH):
        assert by[a].proposed_change is None
    assert "No patch available" in by[ActionType.UPDATE_EXISTING_PAGE].rationale
    assert by[ActionType.OBSERVE].proposed_change["kind"] == "observe"


async def test_stale_and_inference_evidence_are_not_assertable():
    items = [EvidenceItem(id="a", type="owned", status="stale", excerpt=SAML),
             EvidenceItem(id="b", type="inference", status="live", excerpt=SCIM),
             EvidenceItem(id="c", type="profound", status="live", excerpt="visibility fell 24 points overall"),
             EvidenceItem(id="d", type="owned", status="live", excerpt=SCIM)]
    fb = build_fact_base(items)
    assert [f.evidence_id for f in fb.facts] == ["d"]


async def test_manual_actions_and_alternatives(session):
    _, inc, _, _ = await seed(session, with_third=True)
    rows = await propose_interventions(session, inc, decision(ActionType.PUBLISHER_OUTREACH), use_default_llm=False)
    by = {r.action: r for r in rows}
    out = ProposedChange.model_validate(by[ActionType.PUBLISHER_OUTREACH].proposed_change)
    assert out.kind == "manual_task" and THIRD in out.manual_task.body and SAML in out.manual_task.body
    assert out.manual_task.recipient == "directory.example"
    sd = ProposedChange.model_validate(by[ActionType.STRUCTURED_DATA].proposed_change)
    assert sd.manual_task.payload["jsonld"]["@type"] == "FAQPage"
    cmp_ = ProposedChange.model_validate(by[ActionType.CREATE_COMPARISON_CONTENT].proposed_change)
    assert cmp_.files[0].path == "compare/testco-example-saml-single-sign-on.md" or cmp_.files[0].path.startswith("compare/")
    assert by[ActionType.PUBLISHER_OUTREACH].selected and not by[ActionType.OBSERVE].selected


GOOD = {
    "action": "update_existing_page", "title": "Explain SAML SSO", "summary": "Adds SAML SSO details.",
    "files": [{"path": "enterprise/security.md", "change_type": "update",
               "new_content": "## SAML single sign-on\n\nTestCo supports SAML 2.0 single sign-on on the Enterprise plan.\n"}],
    "claims": [{"text": "TestCo supports SAML 2.0 single sign-on on the Enterprise plan", "fact_ids": []}],
}


async def llm_run(session, payload):
    _, inc, ev, _ = await seed(session)
    if isinstance(payload, dict):
        payload = {**payload, "claims": [{"text": c["text"], "fact_ids": [f"{ev[0].id}#0"]} for c in payload.get("claims", [])]}
    llm = FakeLLM(payload)
    rows = await propose_interventions(session, inc, decision(), llm=llm)
    return llm, next(r for r in rows if r.selected)


async def test_llm_patch_used_when_grounded_and_only_called_for_selected(session):
    llm, row = await llm_run(session, GOOD)
    pc = ProposedChange.model_validate(row.proposed_change)
    assert llm.calls == 1 and pc.generated_by == "llm" and pc.title == "Explain SAML SSO"
    assert "<!-- aeo-sre:begin" in pc.files[0].new_content  # markers added by us, not the model


@pytest.mark.parametrize("mutate", [
    lambda p: {**p, "files": [{**p["files"][0], "new_content": "## SSO\n\nSupports SAML 3.5 and SOC2 audits.\n"}]},
    lambda p: {**p, "files": [{**p["files"][0], "path": ".github/workflows/x.md"}]},
    lambda p: {**p, "claims": [{"text": "TestCo guarantees 99.99 percent uptime", "fact_ids": []}]},
    lambda p: {**p, "files": [{**p["files"][0], "new_content": "<!-- aeo-sre:begin id=\"x\" -->hi"}]},
    lambda p: {"title": "x"},
], ids=["invented-numbers", "outside-allowed-files", "unsupported-claim", "marker-injection", "schema-invalid"])
async def test_ungrounded_or_invalid_llm_output_falls_back_to_template(session, mutate):
    llm, row = await llm_run(session, mutate(GOOD))
    pc = ProposedChange.model_validate(row.proposed_change)
    assert llm.calls == 1 and pc.generated_by == "template"
    assert any("LLM draft rejected" in n for n in pc.notes)
    assert SAML in pc.files[0].new_content


async def test_llm_unavailable_falls_back_to_template(session):
    llm, row = await llm_run(session, LLMUnavailable("down"))
    pc = ProposedChange.model_validate(row.proposed_change)
    assert pc.generated_by == "template" and any("LLM unavailable" in n for n in pc.notes)


async def test_repropose_updates_in_place_and_never_rewrites_decided(session):
    _, inc, _, _ = await seed(session)
    first = await propose_interventions(session, inc, decision(), use_default_llm=False)
    await session.commit()
    again = await propose_interventions(session, inc, decision(ActionType.CREATE_FAQ), use_default_llm=False)
    await session.commit()
    assert {r.id for r in first} == {r.id for r in again}
    assert next(r for r in again if r.selected).action is ActionType.CREATE_FAQ
    upd = next(r for r in again if r.action is ActionType.UPDATE_EXISTING_PAGE)
    session.add(Approval(intervention_id=upd.id, status="approved", decided_by="a", decided_actor_type="human"))
    await session.commit()
    before = upd.title, upd.score
    await propose_interventions(session, inc, decision(ActionType.UPDATE_EXISTING_PAGE), use_default_llm=False)
    assert (upd.title, upd.score) == before


def test_apply_change_and_diff_semantics():
    sec1 = wrap_section("incident-1", "## A\n\nfirst")
    fc = FileChange(path="p.md", old_content="# T\n\ntext\n", new_content=sec1, patch_mode="upsert_section",
                    section_id="incident-1")
    once = apply_change(fc.old_content, fc)
    assert once.startswith("# T\n\ntext\n\n<!-- aeo-sre:begin")
    assert apply_change(once, fc) == once  # idempotent
    sec2 = wrap_section("incident-1", "## A\n\nsecond")
    fc2 = fc.model_copy(update={"new_content": sec2})
    replaced = apply_change(once, fc2)
    assert "second" in replaced and "first" not in replaced and replaced.count("aeo-sre:begin") == 1
    d = build_diff([fc])
    assert d.startswith("diff --git a/p.md b/p.md\n--- a/p.md\n+++ b/p.md\n") and "+## A" in d and " text" in d
    created = FileChange(path="n.md", new_content="x\n", change_type="create")
    assert "--- /dev/null" in build_diff([created]) and "+x" in build_diff([created])


def test_path_validation():
    assert validate_path("content/a.md") is None
    for bad in (".github/workflows/ci.yml", "../x.md", "/etc/passwd.md", "a/.env.md", "src/app.py", "a//b.md"):
        assert validate_path(bad) is not None
