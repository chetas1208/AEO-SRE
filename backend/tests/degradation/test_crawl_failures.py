"""Crawl failures must yield unavailable/failed evidence with NO fabricated content or scores."""
from __future__ import annotations

import httpx
import pytest
from app.domain.enums import EvidenceStatus
from app.investigation.collector import collect_evidence
from app.models.evidence import Evidence
from sqlalchemy import select

from tests import factories as f

OWN = "https://testco-fail.example"


@pytest.fixture
async def incident(session):
    org = await f.make_org(session, domain="testco-fail.example", competitor_domains=["rival-fail.example"])
    cluster = await f.make_prompt_cluster(session, org, "Enterprise SSO")
    return await f.make_incident(
        session, org, prompt_cluster_id=cluster.id, title="Enterprise SSO visibility drop",
        context={"cited_urls": [f"{OWN}/docs/sso", "https://rival-fail.example/sso"]},
    )


def _mock_all(mock_http, status=None, exc=None):
    mock_http.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r".*sitemap.*").mock(return_value=httpx.Response(404))
    route = mock_http.get(url__regex=r".*").mock(
        side_effect=exc) if exc else mock_http.get(url__regex=r".*").mock(return_value=httpx.Response(status))
    return route


@pytest.mark.parametrize("status", [403, 404, 500, 503])
async def test_http_errors_become_unavailable_or_failed_evidence(session, incident, web_collector, mock_http,
                                                                 emit, status):
    _mock_all(mock_http, status=status)
    rows = await collect_evidence(session, incident, emit, collector=web_collector, use_cache=False)
    await session.commit()
    assert rows, "collector should still record what it attempted"
    for r in rows:
        assert r.status in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value), (r.url, r.status)
        assert not r.excerpt, "no content may be inferred from a failed fetch"
        assert r.content_hash is None
        assert r.support_score is None and r.contradiction_score is None
        assert (r.confidence or 0) == 0
    assert emit.statuses() & {"warning", "failed"}, "failures must be surfaced in the event log"


@pytest.mark.parametrize("exc", [httpx.ConnectTimeout("t"), httpx.ConnectError("refused"), httpx.ReadTimeout("slow")])
async def test_network_errors_become_failed_evidence(session, incident, web_collector, mock_http, emit, exc):
    _mock_all(mock_http, exc=exc)
    rows = await collect_evidence(session, incident, emit, collector=web_collector, use_cache=False)
    assert rows
    assert all(r.status in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value) for r in rows)
    assert all(not r.excerpt and r.content_hash is None for r in rows)


async def test_failed_evidence_cannot_confirm_a_root_cause(session, incident, web_collector, mock_http, emit):
    from app.investigation.evidence_gate import confirm_aeo_root_cause

    _mock_all(mock_http, status=500)
    rows = await collect_evidence(session, incident, emit, collector=web_collector, use_cache=False)
    hyp = await f.make_hypothesis(session, incident, evidence_ids=[r.id for r in rows], confidence=0.9,
                                  rationale="A long and detailed rationale that claims a cause exists.")
    result = confirm_aeo_root_cause(hyp, rows)
    assert result.confirmed is False
    assert result.missing


async def test_partial_failure_keeps_good_pages_and_marks_bad_ones(session, incident, web_collector, mock_http, emit):
    mock_http.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r".*sitemap.*").mock(return_value=httpx.Response(404))
    html = "<html><head><title>SSO</title></head><body><h1>Enterprise SSO</h1><p>We support SAML SSO.</p></body></html>"
    mock_http.get(f"{OWN}/docs/sso").mock(return_value=httpx.Response(200, text=html, headers={"content-type": "text/html"}))
    mock_http.get(url__regex=r".*").mock(return_value=httpx.Response(503))
    rows = await collect_evidence(session, incident, emit, collector=web_collector, use_cache=False)
    by_url = {r.url: r for r in rows}
    good = by_url.get(f"{OWN}/docs/sso")
    assert good is not None and good.status in (EvidenceStatus.LIVE.value, EvidenceStatus.CHANGED.value)
    assert good.content_hash and len(good.content_hash) == 64
    bad = [r for r in rows if r is not good]
    assert bad and all(r.status in (EvidenceStatus.UNAVAILABLE.value, EvidenceStatus.FAILED.value) for r in bad)


async def test_no_domains_no_fetch_no_evidence(session, web_collector, mock_http, emit):
    org = await f.make_org(session, domain="", competitor_domains=[], canonical_domains=[])
    inc = await f.make_incident(session, org, context={})
    rows = await collect_evidence(session, inc, emit, collector=web_collector, use_cache=False)
    assert rows == []
    assert len(mock_http.calls) == 0


async def test_ssrf_private_urls_never_fetched(session, web_collector, mock_http, emit):
    org = await f.make_org(session, domain="testco-ssrf.example")
    inc = await f.make_incident(session, org, context={"cited_urls": ["http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data"]})
    mock_http.get(url__regex=r".*").mock(return_value=httpx.Response(404))
    rows = await collect_evidence(session, inc, emit, collector=web_collector, use_cache=False)
    hosts = [str(c.request.url.host) for c in mock_http.calls]
    assert "127.0.0.1" not in hosts and "169.254.169.254" not in hosts
    del rows
    assert (await session.execute(select(Evidence))).scalars().all() is not None
