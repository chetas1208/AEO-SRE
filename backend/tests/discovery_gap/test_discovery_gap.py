"""Discovery gap: perception adapter, analyzer, service (incidents/evidence/idempotency), failure honesty."""
from __future__ import annotations

import copy
from datetime import timedelta

import httpx
import pytest
from app.changeguard import canonical
from app.connectors.profound import ProfoundClient
from app.connectors.profound.errors import ProfoundPermissionError
from app.core.db import utcnow
from app.discovery_gap import analyzer as an
from app.discovery_gap import perception as pc
from app.discovery_gap import service as svc
from app.models.core import AuditEvent, Incident
from app.models.evidence import Evidence
from sqlalchemy import func, select

from tests import factories as f
from tests.discovery_gap.conftest import BASE, CAT, FIXTURE, HUMAN, FakeProfound, add_canonical

MODE = "SIMULATED"  # these tests never touch the real API


async def run(session, org, client, **kw):
    kw.setdefault("source_mode", MODE)
    kw.setdefault("category_id", CAT)
    kw.setdefault("use_default_semantics", False)
    rep = await svc.run_discovery_gap(session, org.id, client=client, **kw)
    await session.commit()
    return rep


async def count(session, model, *where):
    return (await session.execute(select(func.count()).select_from(model).where(*where))).scalar_one()


# ------------------------------------------------------------------ perception (pure)
def test_normalize_factcheck_claims_shapes():
    got = pc.normalize_factcheck_claims(FIXTURE, window=("2026-09-20", "2026-10-02"), source_mode=MODE)
    assert [c.occurrence for c in got] == [7, 4, 1]
    first = got[0]
    assert first.engines == ["ChatGPT", "Google Gemini"] and first.source_mode == MODE
    assert first.citation_sources == [{"domain": "reviews.example", "url": "https://reviews.example/testco-sso"},
                                      {"domain": "forum.example", "url": "https://forum.example/t/1"}]
    assert first.origin == pc.ORIGIN_FACTCHECK and first.observed_at is not None


def test_normalize_drops_blank_and_one_word_claims():
    assert pc.normalize_factcheck_claims({"data": [{"claim": ""}, {"claim": "x"}, {"occurrence": 3}]}) == []


def test_answer_claims_require_brand_and_known_feature_and_strip_markdown():
    payload = {"data": [{"model": "ChatGPT", "date": "2026-10-01", "prompt": "p", "citations": ["https://a.example/x"],
                         "response": "- **TestCo** does not support [SAML SSO](https://x.example) on any plan.\n"
                                     "- Rival does not support SAML SSO on any plan.\nTestCo is nice."}]}
    got = pc.normalize_answer_claims(payload, brand_terms=["testco"])
    assert len(got) == 1 and "TestCo" in got[0].text and "](" not in got[0].text and "**" not in got[0].text
    assert got[0].confidence_cap == pc.ANSWER_CONFIDENCE_CAP and got[0].origin == pc.ORIGIN_ANSWER
    assert pc.normalize_answer_claims(payload, brand_terms=[]) == []


# ------------------------------------------------------------------ honesty: no canonical truth
async def test_no_canonical_truth_skips_without_calling_profound_or_creating_anything(session, org):
    fake = FakeProfound(claims=FIXTURE)
    rep = await run(session, org, fake)
    assert rep.status == "skipped_no_canonical_truth" and rep.created == [] and fake.calls == []
    assert await count(session, Incident) == 0


async def test_retired_and_expired_canonical_do_not_count(session, org):
    c = await add_canonical(session, org)
    await canonical.update_claim(session, c, HUMAN, retire=True)
    await add_canonical(session, org, key="old", statement="SAML SSO is available on the Enterprise plan.",
                        valid_from=utcnow() - timedelta(days=30), valid_until=utcnow() - timedelta(days=1))
    await session.commit()
    rep = await run(session, org, FakeProfound(claims=FIXTURE))
    assert rep.status == "skipped_no_canonical_truth" and await count(session, Incident) == 0


# ------------------------------------------------------------------ end to end
async def test_gap_becomes_incident_with_live_evidence_and_audit(session, org):
    c = await add_canonical(session, org)
    rep = await run(session, org, FakeProfound(claims=FIXTURE))
    assert rep.status == "ok" and len(rep.created) == 1 and rep.source_mode == MODE
    assert rep.analysis["perceived_unique"] == 3 and len(rep.gaps) == 1
    inc = (await session.execute(select(Incident))).scalars().one()
    assert inc.category == "factual_conflict" and inc.state == "detected" and inc.org_id == org.id
    assert inc.title.startswith("DISCOVERY GAP: we state") and "does not support SAML SSO" in inc.title
    g = inc.context["discovery_gap"]
    assert g["canonical_claim_id"] == str(c.id) and g["kind"] == "conflict" and g["occurrence"] == 7
    assert g["engines"] == ["ChatGPT", "Google Gemini"] and g["decided_by"] == "rules"
    assert {d["domain"] for d in g["citation_domains"]} == {"reviews.example", "forum.example"}
    assert inc.context["signature"]["incident_type"] == "discovery_gap" and inc.fingerprint
    ev = (await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars().one()
    assert ev.type == "profound" and ev.status == "live" and ev.excerpt == "TestCo does not support SAML SSO on any plan."
    assert ev.raw["source_mode"] == MODE and ev.raw["canonical_claim"]["statement"].startswith("SAML SSO")
    assert ev.raw["perceived_claim"] == ev.excerpt and ev.retrieval_method == "profound.factcheck_claims"
    events = (await session.execute(select(AuditEvent.event).where(AuditEvent.entity_id == str(inc.id)))).scalars().all()
    assert "incident.detected" in events


async def test_rerun_creates_no_duplicates_and_new_data_updates_in_place(session, org):
    await add_canonical(session, org)
    fake = FakeProfound(claims=FIXTURE)
    first = await run(session, org, fake)
    again = await run(session, org, fake)
    assert len(first.created) == 1 and again.created == [] and again.unchanged == first.created
    assert await count(session, Incident) == 1 and await count(session, Evidence) == 1
    grown = copy.deepcopy(FIXTURE)
    grown["data"][0]["occurrence"] = 12
    third = await run(session, org, FakeProfound(claims=grown))
    assert third.created == [] and third.updated == first.created
    assert await count(session, Incident) == 1 and await count(session, Evidence) == 1
    inc = (await session.execute(select(Incident))).scalars().one()
    assert inc.context["discovery_gap"]["occurrence"] == 12


async def test_two_distinct_gaps_for_one_canonical_claim_are_two_incidents(session, org):
    await add_canonical(session, org)
    two = copy.deepcopy(FIXTURE)
    two["data"].append({"claim": "TestCo has no SAML SSO.", "occurrence": 2, "models": ["Perplexity"]})
    rep = await run(session, org, FakeProfound(claims=two))
    assert len(rep.created) == 2 and await count(session, Incident) == 2
    assert len((await run(session, org, FakeProfound(claims=two))).created) == 0


async def test_dismissed_gap_is_not_reopened(session, org):
    await add_canonical(session, org)
    fake = FakeProfound(claims=FIXTURE)
    await run(session, org, fake)
    inc = (await session.execute(select(Incident))).scalars().one()
    inc.state = "dismissed"
    await session.commit()
    rep = await run(session, org, fake)
    assert rep.created == [] and len(rep.suppressed_dismissed) == 1 and await count(session, Incident) == 1


async def test_dry_run_persists_nothing(session, org):
    await add_canonical(session, org)
    rep = await run(session, org, FakeProfound(claims=FIXTURE), dry_run=True)
    assert len(rep.gaps) == 1 and rep.created == [] and await count(session, Incident) == 0


async def test_org_isolation(session, org, org_b):
    await add_canonical(session, org)
    await canonical.create_claim(session, org_b.id, HUMAN, key="seats", statement="Unlimited seats are included.")
    await session.commit()
    a = await run(session, org, FakeProfound(claims=FIXTURE))
    b = await run(session, org_b, FakeProfound(claims=FIXTURE))
    assert len(a.created) == 1 and b.created == []  # B's canonical claims do not conflict with this perception
    inc = (await session.execute(select(Incident))).scalars().one()
    assert inc.org_id == org.id
    other = await f.make_org(session)
    skipped = await run(session, other, FakeProfound(claims=FIXTURE))
    assert skipped.status == "skipped_no_canonical_truth"  # A's canonical truth never leaks to another org


async def test_compatible_and_unrelated_perceptions_create_nothing(session, org):
    await add_canonical(session, org)
    ok = {"data": [FIXTURE["data"][1], FIXTURE["data"][2]]}
    rep = await run(session, org, FakeProfound(claims=ok))
    assert rep.status == "ok" and rep.gaps == [] and await count(session, Incident) == 0


# ------------------------------------------------------------------ semantic degradation is visible
async def test_ambiguous_pair_without_semantic_layers_is_degraded_not_passed(session, org):
    await add_canonical(session, org)
    amb = {"data": [{"claim": "TestCo does not support Okta login.", "occurrence": 3, "models": ["ChatGPT"]}]}
    rep = await run(session, org, FakeProfound(claims=amb))
    assert rep.analysis["semantic_check"] == "degraded"
    assert {"no_ranker", "no_gateway"} & set(rep.analysis["degraded_reasons"])


async def test_unknown_ids_from_engine_are_rejected(session, org, monkeypatch):
    c = await add_canonical(session, org)
    per = pc.normalize_factcheck_claims(FIXTURE, source_mode=MODE)
    real = an.evaluate_canonical

    def bogus(proposed, canon, **kw):
        res = real(proposed, canon, **kw)
        res.findings[0].canonical_claim_id = "not-a-real-id"
        return res

    monkeypatch.setattr(an, "evaluate_canonical", bogus)
    out = an.analyze(org.id, per, [{"id": str(c.id), "key": c.key, "statement": c.statement, "status": "active"}])
    assert out.gaps == []


# ------------------------------------------------------------------ failures: no fixture fallback, exact reasons
def real_client(mock_http, claims=None, answers=None):
    if claims is not None:
        mock_http.post(f"{BASE}/v2/reports/factcheck/claims").mock(return_value=claims)
    if answers is not None:
        mock_http.post(f"{BASE}/v2/prompts/answers").mock(return_value=answers)
    return ProfoundClient(api_key="test-key-not-real", backoff_base=0.01)


async def test_factcheck_403_and_answers_down_reports_exact_reason_and_creates_nothing(session, org, mock_http):
    await add_canonical(session, org)
    client = real_client(mock_http, httpx.Response(403, json={"detail": "FactCheck not enabled for this category"}),
                         httpx.Response(403, json={"detail": "no"}))
    rep = await run(session, org, client)
    await client.aclose()
    assert rep.status == "skipped_factcheck_unavailable"
    assert "HTTP 403" in rep.reason and "FactCheck not enabled" in rep.reason
    assert rep.perception["factcheck_state"] == "unavailable" and rep.perception["answers_state"] == "unavailable"
    assert rep.gaps == [] and await count(session, Incident) == 0  # the recorded fixture is never substituted


async def test_factcheck_unavailable_falls_back_to_answer_text_at_lower_confidence(session, org, mock_http):
    await add_canonical(session, org)
    answers = {"data": [{"model": "ChatGPT", "date": "2026-10-01", "prompt": "does testco support sso",
                         "citations": ["https://reviews.example/x"],
                         "response": "TestCo does not support SAML SSO on any plan."}]}
    client = real_client(mock_http, httpx.Response(422, json={"detail": "FactCheck is not set up"}),
                         httpx.Response(200, json=answers))
    rep = await run(session, org, client)
    await client.aclose()
    assert rep.status == "ok" and rep.perception["source"] == "answer_text" and len(rep.created) == 1
    assert "lower confidence" in rep.reason
    g = rep.gaps[0]
    assert g["kind"] == "uncertain" and g["confidence"] <= pc.ANSWER_CONFIDENCE_CAP
    assert g["evidence_grade"] == "answer_text_lower_confidence"
    ev = (await session.execute(select(Evidence))).scalars().one()
    assert ev.retrieval_method == "profound.answers" and ev.contradiction_score is None


async def test_factcheck_empty_does_not_fabricate_and_answers_fallback_labelled(session, org, mock_http):
    await add_canonical(session, org)
    client = real_client(mock_http, httpx.Response(200, json={"info": {"total_results": 0}, "data": []}),
                         httpx.Response(200, json={"data": []}))
    rep = await run(session, org, client)
    await client.aclose()
    assert rep.status == "ok" and rep.gaps == [] and rep.perception["factcheck_state"] == "empty"
    assert "no AI-perceived claims" in rep.reason and await count(session, Incident) == 0


async def test_profound_not_configured_makes_no_request(session, org, mock_http):
    await add_canonical(session, org)
    client = ProfoundClient(api_key="")
    rep = await run(session, org, client)
    await client.aclose()
    assert rep.status == "skipped_profound_not_configured" and not mock_http.calls and await count(session, Incident) == 0


async def test_rate_limit_is_reported_not_swallowed(session, org):
    await add_canonical(session, org)
    from app.connectors.profound.errors import ProfoundRateLimited

    rep = await run(session, org, FakeProfound(claims_error=ProfoundRateLimited("slow down", retry_after=999, status=429)))
    assert rep.status == "skipped_rate_limited" and rep.gaps == []


async def test_no_matching_profound_category_is_reported(session, org, mock_http):
    await add_canonical(session, org)
    mock_http.get(f"{BASE}/v1/org/categories").mock(return_value=httpx.Response(200, json=[]))
    client = ProfoundClient(api_key="test-key-not-real")
    rep = await run(session, org, client, category_id=None)
    await client.aclose()
    assert rep.status == "skipped_no_profound_category" and rep.reason.startswith("no_profound_category_owns_")


async def test_reason_uses_error_type_name():
    exc = ProfoundPermissionError("forbidden", status=403)
    assert pc._unavailable_reason(exc).startswith("PermissionError: forbidden (HTTP 403)")


# ------------------------------------------------------------------ worker registration
def test_job_registered():
    from app.workers.jobs import HANDLERS
    from app.workers.queue import JOB_KINDS

    assert "detect_discovery_gaps" in HANDLERS and "detect_discovery_gaps" in JOB_KINDS
