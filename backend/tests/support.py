"""Shared test-only world builders for loop/degradation tests (RECORDED/TEST ONLY data; external HTTP via respx)."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
from app.domain.enums import IncidentState

from tests import factories as f

GH = "https://api.github.com/repos/acme/site"
HTML = {"content-type": "text/html"}


class FixtureRanker:
    """Stands in for the (untrained) EvidenceRanker artifact. TEST ONLY: deterministic keyword scorer."""

    available = True

    def score(self, claim, passage, meta=None):
        hit = "saml" in passage.lower()
        return {"support": 0.9 if hit else 0.1, "contradiction": 0.02, "insufficient": 0.08, "freshness_risk": 0.2}


def mock_github(router):
    router.get(f"{GH}/git/ref/heads/main").respond(200, json={"object": {"sha": "basesha"}})
    router.get(f"{GH}/pulls").respond(200, json=[])
    router.get(url__regex=rf"{GH}/git/ref/heads/aeo.*").respond(404, json={"message": "Not Found"})
    router.post(f"{GH}/git/refs").respond(201, json={})
    router.get(url__regex=rf"{GH}/contents/.*").respond(404, json={"message": "Not Found"})
    router.put(url__regex=rf"{GH}/contents/.*").respond(201, json={"commit": {"sha": "c1"}})
    router.post(f"{GH}/pulls").respond(201, json={"number": 7, "html_url": "https://github.com/acme/site/pull/7"})


async def seed_world(session, mock_http, monkeypatch, *, own_has_fact: bool = True):
    """Org + RECORDED signal series + recorded web pages. Returns (org, cluster, now)."""
    import app.investigation.collector as col

    monkeypatch.setattr(col, "_load_ranker", lambda: FixtureRanker())
    now = datetime.now(UTC)
    org = await f.make_org(session, name="LoopCo", domain="loopco.example", competitor_domains=["rival-loop.example"],
                           canonical_domains=["loopco.example"], topics=["Enterprise SSO"])
    cluster = await f.make_prompt_cluster(
        session, org, "Enterprise SSO", ["best enterprise SSO SAML provider", "does LoopCo support SAML SSO"])
    await f.make_signal_series(session, org, metric="visibility", values=f.visibility_drop_values(61, 37),
                               end=now - timedelta(hours=2), cluster=cluster)
    await f.make_signal_series(session, org, metric="competitor_share", values=f.visibility_drop_values(21, 54),
                               end=now - timedelta(hours=2), cluster=cluster)
    # a previous crawl of the rival page (older content) so the new fetch is a genuine CHANGE
    old = await f.make_incident(session, org, state=IncidentState.CLOSED, title="earlier incident (fixture)")
    await f.make_evidence(session, old, type="competitor", url="https://rival-loop.example/",
                          title="Rival", content_hash="a" * 64, retrieved_at=now - timedelta(days=10),
                          raw={"block_index": [{"heading": "Enterprise SSO", "hash": "b" * 64}]})
    mock_http.get(url__regex=r".*/robots\.txt").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r".*sitemap.*").mock(return_value=httpx.Response(404))
    lm = {"last-modified": (now - timedelta(days=2)).strftime("%a, %d %b %Y %H:%M:%S GMT")}
    rival = ("<html><head><title>Rival Enterprise SSO</title></head><body><h1>Enterprise SSO</h1>"
             "<p>Rival now supports SAML SSO and SCIM provisioning for enterprise SSO.</p></body></html>")
    own = ("<html><head><title>LoopCo Security</title></head><body><h1>Security</h1>"
           "<h2>Enterprise SSO</h2><p>LoopCo supports SAML SSO with Okta and Azure AD on the Enterprise plan.</p></body></html>"
           if own_has_fact else
           "<html><head><title>LoopCo</title></head><body><h1>LoopCo</h1><p>Welcome to our website.</p></body></html>")
    mock_http.get(url__regex=r"https://rival-loop\.example/?").mock(
        return_value=httpx.Response(200, text=rival, headers={**HTML, **lm}))
    mock_http.get(url__regex=r"https://(www\.)?loopco\.example/?$").mock(
        return_value=httpx.Response(200, text=own, headers={**HTML, **lm}))
    mock_http.get(url__regex=r"https://(www\.)?loopco\.example/.+").mock(return_value=httpx.Response(404))
    mock_http.get(url__regex=r"https://rival-loop\.example/.+").mock(return_value=httpx.Response(404))
    return org, cluster, now


