"""Review categories, investigation budget, and answer-detail skip."""

from datetime import UTC, datetime, timedelta

import pytest
from app.evidence.provenance import edge_provenance
from app.investigation.budget import budget_exhausted, independence_note
from app.learning.review import UnknownRejectionReason, acceptance_from_counts, normalize_reason


def test_rejection_categories_are_closed():
    assert normalize_reason(None) == "other"
    assert normalize_reason("insufficient_evidence") == "insufficient_evidence"
    with pytest.raises(UnknownRejectionReason):
        normalize_reason("delete_competitor_site")


def test_acceptance_is_a_ratio_not_a_reward():
    assert acceptance_from_counts(0, 0) == (None, 0)
    rate, n = acceptance_from_counts(3, 1)
    assert n == 4 and rate == 0.75


def test_budget_stops_after_the_limit():
    start = datetime(2026, 10, 2, tzinfo=UTC)
    assert budget_exhausted(start, start + timedelta(seconds=10), 180) is False
    assert budget_exhausted(start, start + timedelta(seconds=181), 180) is True


def test_edge_hash_uses_the_extract_when_no_body_was_stored():
    prov = edge_provenance(
        source="rca", extract="This hypothesis cites no evidence.", confidence=0.2,
        retrieval_method="rca",
    )
    assert prov["hash"]
    assert prov["extract"].startswith("This hypothesis")


def test_one_evidence_family_is_not_independent_confirmation():
    note = independence_note(["profound", "profound"])
    assert note is not None and "one family" in note
    assert independence_note(["profound", "owned"]) is not None
    assert "one family" not in (independence_note(["profound", "owned"]) or "")


@pytest.mark.asyncio
async def test_answer_details_do_not_call_profound_without_a_key(monkeypatch):
    from app.core.config import Settings, get_settings
    from app.investigation.answers import record_answer_details

    get_settings.cache_clear()
    monkeypatch.setenv("PROFOUND_API_KEY", "")
    monkeypatch.setattr("app.investigation.answers.get_settings", lambda: Settings(profound_api_key=""))
    out = await record_answer_details(None, __import__("uuid").uuid4())
    assert out["answer_rows"] == 0
    assert out["requests"] == 0
    assert out["reason"] == "profound_not_configured"
    get_settings.cache_clear()
