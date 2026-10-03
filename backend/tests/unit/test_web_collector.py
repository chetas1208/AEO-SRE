# ruff: noqa: E501
"""WebCollector + collect_evidence unit tests. HTTP is mocked with respx (fixtures only; no real network).

All page bodies below are synthetic test fixtures, not representations of any real site.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from app.connectors.web import ResultCache, WebCollector, diff_snapshots, validate_url
from app.connectors.web.extract import extract_html
from app.connectors.web.ssrf import UnsafeURL, is_public_ip
from app.connectors.web.types import Block, FetchResult
from app.domain.enums import EvidenceStatus, EvidenceType, StepStatus
from app.investigation.collector import (
    EvidenceRowSnapshotStore,
    IncidentContext,
    build_query_terms,
    classify_domain,
    collect_evidence,
    score_text,
)
from app.models.core import Incident, Organization
from app.models.evidence import Evidence
from sqlalchemy import select

PUBLIC_IP = "93.184.216.34"
PRIVATE_HOSTS = {"intranet.test": "10.1.2.3", "meta.test": "169.254.169.254", "loop.test": "127.0.0.1"}


async def fake_resolver(host: str) -> list[str]:
    if host in PRIVATE_HOSTS:
        return [PRIVATE_HOSTS[host]]
    return [PUBLIC_IP]


def page(title: str, body: str, extra_head: str = "") -> str:
    return f"<html><head><title>{title}</title>{extra_head}</head><body><nav>Home Pricing Login</nav>{body}</body></html>"


PRICING = page(
    "Acme Pricing",
    "<main><h1>Acme Pricing</h1><p>Acme offers three plans for teams of every size, with transparent monthly "
    "billing and no hidden fees for any customer.</p><h2>Enterprise SSO</h2><p>Enterprise SSO via SAML 2.0 and "
    "SCIM provisioning is included in the Enterprise plan at $49 per seat per month.</p><h2>Support</h2>"
    "<p>Email support is included on every plan, and priority support is available for Enterprise customers.</p>"
    "</main><footer>Copyright Acme</footer>",
)


def make(mock_http, **kw) -> WebCollector:
    kw.setdefault("per_host_min_interval", 0.0)
    kw.setdefault("cache", ResultCache(use_redis=False))
    return WebCollector(resolver=fake_resolver, **kw)


def robots(mock_http, host: str, body: str = "User-agent: *\nAllow: /\n", status: int = 200):
    return mock_http.get(f"https://{host}/robots.txt").mock(return_value=httpx.Response(status, text=body))


# ---- SSRF --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "ip,expected",
    [
        ("93.184.216.34", True),
        ("10.0.0.1", False),
        ("127.0.0.1", False),
        ("169.254.169.254", False),
        ("192.168.1.1", False),
        ("172.16.0.5", False),
        ("100.64.0.1", False),
        ("::1", False),
        ("fe80::1", False),
        ("fc00::1", False),
        ("::ffff:127.0.0.1", False),
        ("0.0.0.0", False),
        ("224.0.0.1", False),
        ("2606:4700:4700::1111", True),
    ],
)
def test_is_public_ip(ip, expected):
    assert is_public_ip(ip) is expected


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "gopher://example.com",
        "javascript:alert(1)",
        "http://127.0.0.1/",
        "http://10.0.0.8/admin",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://localhost/",
        "http://foo.internal/",
        "http://user:pw@example.com/",
        "https://example.com:22/",
        "http://intranet.test/",
        "http://meta.test/",
        "http:///nohost",
        "//example.com",
    ],
)
async def test_validate_url_rejects(url):
    with pytest.raises(UnsafeURL):
        await validate_url(url, fake_resolver)


async def test_validate_url_accepts_public_and_strips_fragment():
    assert await validate_url("https://example.com/a#frag", fake_resolver) == "https://example.com/a"


async def test_fetch_private_target_is_unavailable_and_never_requested(mock_http):
    route = mock_http.get(url__regex=r".*").mock(return_value=httpx.Response(200, text="secret"))
    async with make(mock_http) as c:
        for url in (
            "http://intranet.test/",
            "http://169.254.169.254/latest/meta-data/",
            "file:///etc/passwd",
        ):
            res = await c.fetch(url)
            assert res.status == EvidenceStatus.UNAVAILABLE.value
            assert res.text == "" and res.content_hash is None
            assert "blocked" in (res.error or "")
    assert not route.called


async def test_redirect_to_private_address_is_blocked(mock_http):
    robots(mock_http, "public.test")
    mock_http.get("https://public.test/go").mock(
        return_value=httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})
    )
    meta = mock_http.get(url__regex=r"http://169\.254\.169\.254/.*").mock(
        return_value=httpx.Response(200, text="x")
    )
    async with make(mock_http) as c:
        res = await c.fetch("https://public.test/go")
    assert res.status == EvidenceStatus.UNAVAILABLE.value
    assert not meta.called


async def test_redirect_to_internal_hostname_via_dns_is_blocked(mock_http):
    robots(mock_http, "public.test")
    mock_http.get("https://public.test/go").mock(
        return_value=httpx.Response(301, headers={"location": "https://intranet.test/x"})
    )
    async with make(mock_http) as c:
        res = await c.fetch("https://public.test/go")
    assert res.status == EvidenceStatus.UNAVAILABLE.value and "non-public" in res.error


async def test_redirect_limit(mock_http):
    robots(mock_http, "loop.example")
    mock_http.get("https://loop.example/a").mock(
        return_value=httpx.Response(302, headers={"location": "https://loop.example/a"})
    )
    async with make(mock_http, max_redirects=3) as c:
        res = await c.fetch("https://loop.example/a")
    assert res.status == EvidenceStatus.FAILED.value and "redirect" in res.error


async def test_redirect_followed_and_final_url_recorded(mock_http):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/old").mock(
        return_value=httpx.Response(301, headers={"location": "/pricing"})
    )
    mock_http.get("https://acme.test/pricing").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html; charset=utf-8"})
    )
    async with make(mock_http) as c:
        res = await c.fetch("https://acme.test/old")
    assert res.ok and res.final_url == "https://acme.test/pricing" and res.url == "https://acme.test/old"


# ---- robots ------------------------------------------------------------------------------------


async def test_robots_disallow_blocks_page_request(mock_http):
    robots(mock_http, "acme.test", "User-agent: *\nDisallow: /private\n")
    page_route = mock_http.get("https://acme.test/private/x").mock(
        return_value=httpx.Response(200, text=PRICING)
    )
    ok_route = mock_http.get("https://acme.test/public").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    async with make(mock_http) as c:
        blocked = await c.fetch("https://acme.test/private/x")
        allowed = await c.fetch("https://acme.test/public")
    assert blocked.status == EvidenceStatus.UNAVAILABLE.value and "robots" in blocked.error
    assert not page_route.called and ok_route.called and allowed.ok


async def test_robots_targeting_our_agent_is_respected(mock_http):
    robots(mock_http, "acme.test", "User-agent: AEO-SRE-Bot\nDisallow: /\n")
    route = mock_http.get("https://acme.test/").mock(return_value=httpx.Response(200, text=PRICING))
    async with make(mock_http) as c:
        res = await c.fetch("https://acme.test/")
    assert res.status == EvidenceStatus.UNAVAILABLE.value and not route.called


async def test_robots_404_allows_and_robots_5xx_fails_closed(mock_http):
    robots(mock_http, "open.test", status=404, body="nope")
    robots(mock_http, "flaky.test", status=503, body="down")
    mock_http.get("https://open.test/").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    closed = mock_http.get("https://flaky.test/").mock(return_value=httpx.Response(200, text=PRICING))
    async with make(mock_http) as c:
        assert (await c.fetch("https://open.test/")).ok
        res = await c.fetch("https://flaky.test/")
    assert res.status == EvidenceStatus.UNAVAILABLE.value and "robots.txt unavailable" in res.error
    assert not closed.called


async def test_robots_can_be_disabled_explicitly(mock_http):
    mock_http.get("https://acme.test/x").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    async with make(mock_http, respect_robots=False) as c:
        assert (await c.fetch("https://acme.test/x")).ok


async def test_user_agent_header_sent(mock_http):
    robots(mock_http, "acme.test")
    route = mock_http.get("https://acme.test/").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    async with make(mock_http) as c:
        await c.fetch("https://acme.test/")
    assert "AEO-SRE-Bot" in route.calls[0].request.headers["user-agent"]


# ---- failed / unavailable fetches never invent content -----------------------------------------


@pytest.mark.parametrize(
    "code,status",
    [
        (500, "failed"),
        (503, "failed"),
        (429, "failed"),
        (404, "unavailable"),
        (410, "unavailable"),
        (403, "unavailable"),
        (401, "unavailable"),
    ],
)
async def test_http_error_statuses(mock_http, code, status):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/p").mock(
        return_value=httpx.Response(code, text="<html>error page</html>")
    )
    async with make(mock_http) as c:
        res = await c.fetch("https://acme.test/p")
    assert res.status == status and res.http_status == code
    assert res.text == "" and res.blocks == [] and res.content_hash is None and res.error


async def test_timeout_and_connect_error_are_failed(mock_http):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/slow").mock(side_effect=httpx.ReadTimeout("slow"))
    mock_http.get("https://acme.test/down").mock(side_effect=httpx.ConnectError("refused"))
    async with make(mock_http) as c:
        slow, down = await c.fetch("https://acme.test/slow"), await c.fetch("https://acme.test/down")
    for res in (slow, down):
        assert res.status == EvidenceStatus.FAILED.value and res.text == "" and res.content_hash is None
    assert "timeout" in slow.error


async def test_oversize_body_fails(mock_http):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/big").mock(
        return_value=httpx.Response(
            200,
            content=b"<html><body>" + b"a" * 5000 + b"</body></html>",
            headers={"content-type": "text/html"},
        )
    )
    async with make(mock_http, max_bytes=1000) as c:
        res = await c.fetch("https://acme.test/big")
    assert res.status == EvidenceStatus.FAILED.value and res.text == ""


async def test_unsupported_content_type_and_empty_js_shell_are_unavailable(mock_http):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/doc.pdf").mock(
        return_value=httpx.Response(200, content=b"%PDF-1.4 ...", headers={"content-type": "application/pdf"})
    )
    mock_http.get("https://acme.test/spa").mock(
        return_value=httpx.Response(
            200,
            text='<html><head><script src="/app.js"></script></head><body><div id="root"></div></body></html>',
            headers={"content-type": "text/html"},
        )
    )
    async with make(mock_http) as c:
        pdf, spa = await c.fetch("https://acme.test/doc.pdf"), await c.fetch("https://acme.test/spa")
    assert pdf.status == "unavailable" and "unsupported" in pdf.error
    assert spa.status == "unavailable" and spa.text == ""


async def test_per_host_rate_limit_spaces_requests(mock_http):
    robots(mock_http, "acme.test")
    for i in range(3):
        mock_http.get(f"https://acme.test/p{i}").mock(
            return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
        )
    import asyncio

    async with make(mock_http, per_host_min_interval=0.15, per_host_concurrency=3) as c:
        t0 = time.monotonic()
        await asyncio.gather(*(c.fetch(f"https://acme.test/p{i}") for i in range(3)))
        elapsed = time.monotonic() - t0
    # robots.txt + 3 pages = 4 request starts, spaced >= 0.15s apart
    assert elapsed >= 0.40


# ---- extraction / hashing ----------------------------------------------------------------------


def test_extract_heading_scoped_blocks_and_noise_removed():
    title, text, blocks, modified, _ = extract_html(PRICING, "https://acme.test/pricing")
    assert title == "Acme Pricing"
    headings = [b.heading for b in blocks]
    assert "Enterprise SSO" in headings and "Support" in headings
    sso = next(b for b in blocks if b.heading == "Enterprise SSO")
    assert "SAML 2.0" in sso.text and "$49" in sso.text and "Email support" not in sso.text
    assert "Copyright Acme" not in text and "Login" not in text


def test_extract_bs4_fallback_for_tiny_pages():
    _, text, blocks, _, method = extract_html("<html><body><h2>Short</h2><p>Tiny page.</p></body></html>")
    assert method == "bs4" and "Tiny page." in text and blocks[0].heading == "Short"


def test_extract_page_modified_date_from_meta_and_jsonld():
    meta = '<meta property="article:modified_time" content="2024-03-01T10:00:00Z">'
    _, _, _, mod, _ = extract_html(page("t", "<main><h1>x</h1><p>" + "word " * 60 + "</p></main>", meta))
    assert mod == datetime(2024, 3, 1, 10, tzinfo=UTC)
    ld = '<script type="application/ld+json">{"@type":"Article","dateModified":"2023-05-05"}</script>'
    _, _, _, mod2, _ = extract_html(page("t", "<p>hello world content here</p>", ld))
    assert mod2 is not None and mod2.year == 2023


async def test_content_hash_is_sha256_of_normalized_text_and_whitespace_stable(mock_http):
    import hashlib

    robots(mock_http, "acme.test")
    variant = PRICING.replace("<p>", "\n   <p>\n").replace("</p>", "\n</p>  ")
    mock_http.get("https://acme.test/a").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    mock_http.get("https://acme.test/b").mock(
        return_value=httpx.Response(200, text=variant, headers={"content-type": "text/html"})
    )
    async with make(mock_http) as c:
        a, b = await c.fetch("https://acme.test/a"), await c.fetch("https://acme.test/b")
    assert a.content_hash == b.content_hash == hashlib.sha256(a.text.encode()).hexdigest()
    assert a.status == "live" and a.http_status == 200 and a.fetched_at.tzinfo is not None


# ---- cache -------------------------------------------------------------------------------------


async def test_cache_hit_avoids_second_request_and_failures_are_not_cached(mock_http):
    robots(mock_http, "acme.test")
    ok = mock_http.get("https://acme.test/ok").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    bad = mock_http.get("https://acme.test/bad").mock(return_value=httpx.Response(500))
    async with make(mock_http) as c:
        first, second = await c.fetch("https://acme.test/ok"), await c.fetch("https://acme.test/ok")
        await c.fetch("https://acme.test/bad"), await c.fetch("https://acme.test/bad")
        forced = await c.fetch("https://acme.test/ok", use_cache=False)
    assert ok.call_count == 2 and bad.call_count == 2  # second ok call was cached; forced refetch counted
    assert not first.from_cache and second.from_cache and second.content_hash == first.content_hash
    assert not forced.from_cache


async def test_cache_falls_back_to_memory_when_redis_down():
    cache = ResultCache(redis_url="redis://127.0.0.1:1/0", ttl_seconds=60)
    res = FetchResult(
        url="https://a.test/",
        final_url="https://a.test/",
        status="live",
        content_hash="h",
        text="t",
        blocks=[Block("H", 2, "body")],
    )
    await cache.set("https://a.test/", res)
    got = await cache.get("https://a.test/")
    assert got is not None and got.from_cache and got.blocks[0].heading == "H" and cache.backend == "memory"
    assert await cache.get("https://missing.test/") is None
    await cache.close()


async def test_cache_ttl_expiry():
    cache = ResultCache(use_redis=False, ttl_seconds=1)
    res = FetchResult(url="u", final_url="u", status="live", content_hash="h", text="t")
    await cache.set("https://a.test/", res, ttl=0)
    assert await cache.get("https://a.test/") is None


# ---- change detection --------------------------------------------------------------------------


def _fr(blocks: list[tuple[str, str]], status="live") -> FetchResult:
    text = " ".join(t for _, t in blocks)
    from app.connectors.web.types import hash_text

    return FetchResult(
        url="u",
        final_url="u",
        status=status,
        text=text if status == "live" else "",
        content_hash=hash_text(text) if status == "live" else None,
        blocks=[Block(h, 2, t, i) for i, (h, t) in enumerate(blocks)],
    )


def test_diff_new_unchanged_changed_unavailable():
    base = _fr([("Pricing", "Pro is $29"), ("SSO", "SSO on Enterprise")])
    assert diff_snapshots(None, base).kind == "new"
    same = diff_snapshots(base, _fr([("Pricing", "Pro is $29"), ("SSO", "SSO on Enterprise")]))
    assert same.kind == "unchanged" and not same.changed
    changed = diff_snapshots(
        base, _fr([("Pricing", "Pro is $39"), ("SSO", "SSO on Enterprise"), ("New", "Added")])
    )
    assert changed.kind == "changed" and changed.changed
    assert changed.modified == [{"heading": "Pricing", "old": "Pro is $29", "new": "Pro is $39"}]
    assert [a["heading"] for a in changed.added] == ["New"] and changed.removed == []
    removed = diff_snapshots(base, _fr([("Pricing", "Pro is $29")]))
    assert [r["heading"] for r in removed.removed] == ["SSO"]
    unavailable = diff_snapshots(base, _fr([], status="failed"))
    assert unavailable.kind == "unavailable" and not unavailable.changed


def test_diff_accepts_dicts_and_evidence_like_rows():
    class Row:
        status = "live"
        content_hash = "old"
        raw = {"block_index": [{"heading": "A", "hash": "h1"}, {"heading": "B", "hash": "h2"}]}

    new = {
        "status": "live",
        "content_hash": "new",
        "blocks": [{"heading": "A", "hash": "h1"}, {"heading": "B", "hash": "h9"}],
    }
    d = diff_snapshots(Row(), new)
    assert d.changed and d.modified[0]["heading"] == "B"
    assert diff_snapshots({"status": "failed", "content_hash": None}, new).kind == "new"


# ---- discovery ---------------------------------------------------------------------------------


async def test_discover_urls_from_robots_sitemap_index_capped_and_same_domain(mock_http):
    robots(mock_http, "acme.test", "User-agent: *\nAllow: /\nSitemap: https://acme.test/sm-index.xml\n")
    mock_http.get("https://acme.test/sm-index.xml").mock(
        return_value=httpx.Response(
            200, text="<sitemapindex><sitemap><loc>https://acme.test/sm-a.xml</loc></sitemap></sitemapindex>"
        )
    )
    urls = "".join(f"<url><loc>https://acme.test/blog/post-{i}</loc></url>" for i in range(30))
    urls += "<url><loc>https://acme.test/pricing</loc></url><url><loc>https://evil.test/x</loc></url>"
    mock_http.get("https://acme.test/sm-a.xml").mock(
        return_value=httpx.Response(200, text=f"<urlset>{urls}</urlset>")
    )
    mock_http.get("https://acme.test/sitemap.xml").mock(return_value=httpx.Response(404))
    async with make(mock_http) as c:
        found = await c.discover_urls("acme.test", limit=5, keywords=["pricing"])
    assert len(found) == 5 and found[0] == "https://acme.test/pricing"
    assert all(u.startswith("https://acme.test/") for u in found)


async def test_discover_urls_missing_sitemap_returns_empty_not_guesses(mock_http):
    robots(mock_http, "acme.test")
    mock_http.get("https://acme.test/sitemap.xml").mock(return_value=httpx.Response(404))
    async with make(mock_http) as c:
        assert await c.discover_urls("acme.test") == []


# ---- relevance + classification ----------------------------------------------------------------


def test_classify_domain():
    own, comp, canon = "acme.com", ["rival.io"], ["docs.acmehelp.org"]
    assert classify_domain("https://www.acme.com/x", own, comp, canon) == EvidenceType.OWNED
    assert classify_domain("https://blog.acme.com/x", own, comp, canon) == EvidenceType.OWNED
    assert classify_domain("https://docs.acmehelp.org/a", own, comp, canon) == EvidenceType.OWNED
    assert classify_domain("https://rival.io", own, comp, canon) == EvidenceType.COMPETITOR
    assert classify_domain("https://notacme.com", own, comp, canon) == EvidenceType.EXTERNAL
    assert classify_domain("https://news.example.org", own, comp, canon) == EvidenceType.EXTERNAL


def test_relevance_scoring_prefers_topic_blocks():
    ctx = IncidentContext(
        org_name="Acme",
        topic="enterprise SSO",
        claim="Acme visibility drop for SSO prompts",
        prompts=["best SSO provider for enterprise", "SAML SSO pricing"],
    )
    terms = build_query_terms(ctx)
    hit, matched = score_text("Enterprise SSO via SAML is included for Acme customers", terms, ctx.topic)
    miss, _ = score_text("We love puppies and sunny weather every day", terms, ctx.topic)
    assert hit > 0.5 > miss and "sso" in matched and miss == 0.0


# ---- collect_evidence (DB) ---------------------------------------------------------------------


class Recorder:
    def __init__(self):
        self.events: list[tuple[str, str, str, dict]] = []

    async def __call__(self, stage, status, message, metadata):
        self.events.append((stage, str(getattr(status, "value", status)), message, metadata))

    @property
    def stages(self):
        return [e[0] for e in self.events]


async def _incident(session) -> Incident:
    org = Organization(
        name="Acme",
        domain="acme.test",
        competitor_domains=["rival.test"],
        canonical_domains=[],
        topics=["enterprise sso"],
    )
    session.add(org)
    await session.flush()
    inc = Incident(
        org_id=org.id,
        title="Enterprise SSO visibility drop",
        summary="Competitor gained SSO citations",
        context={
            "topic": "enterprise SSO",
            "prompts": ["best SSO for enterprise"],
            "cited_urls": ["https://news.example.org/sso-review", "http://127.0.0.1/admin"],
        },
    )
    session.add(inc)
    await session.flush()
    return inc


async def test_collect_evidence_builds_rows_for_owned_competitor_external_and_failures(session, mock_http):
    inc = await _incident(session)
    for host in ("acme.test", "rival.test", "news.example.org"):
        robots(mock_http, host)
        mock_http.get(f"https://{host}/sitemap.xml").mock(return_value=httpx.Response(404))
    mock_http.get("https://acme.test/").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    mock_http.get("https://rival.test/").mock(return_value=httpx.Response(500))
    mock_http.get("https://news.example.org/sso-review").mock(
        return_value=httpx.Response(
            200,
            headers={"content-type": "text/html"},
            text=page(
                "SSO review",
                "<main><h1>SSO review</h1><h2>Enterprise SSO</h2><p>Rival offers enterprise SSO "
                "with SAML and SCIM and was reviewed favourably by independent analysts in the market this year "
                "alongside several other vendors.</p></main>",
            ),
        )
    )
    rec = Recorder()
    calls = []

    def ranker(claim, passage, meta):
        calls.append(meta["type"])
        return {"support": 0.7, "contradiction": 0.1, "insufficient": 0.2, "freshness_risk": 0.3}

    async with make(mock_http) as c:
        rows = await collect_evidence(session, inc, rec, collector=c, ranker=ranker)
    await session.commit()

    by_url = {r.url: r for r in rows}
    own = by_url["https://acme.test/"]
    assert own.type == "owned" and own.status == "live" and own.content_hash
    assert "SAML" in own.excerpt and own.retrieval_method.startswith("httpx")
    assert own.support_score == 0.7 and own.raw["ranker"]["freshness_risk"] == 0.3
    ext = by_url["https://news.example.org/sso-review"]
    assert ext.type == "external" and ext.status == "live"
    rival = by_url["https://rival.test/"]
    assert rival.type == "competitor" and rival.status == "failed"
    assert rival.content_hash is None and rival.excerpt == "" and rival.raw["error"] == "HTTP 500"
    assert rival.support_score is None  # no content => no scores invented
    private = by_url["http://127.0.0.1/admin"]
    assert private.status == "unavailable" and private.excerpt == "" and "blocked" in private.raw["error"]
    assert "owned" in calls and "competitor" not in calls

    persisted = (
        (await session.execute(select(Evidence).where(Evidence.incident_id == inc.id))).scalars().all()
    )
    assert len(persisted) == len(rows) == 4
    for stage in (
        "web.discover.started",
        "web.fetch.started",
        "web.fetch.page",
        "web.fetch.completed",
        "web.relevance.completed",
    ):
        assert stage in rec.stages
    done = next(e for e in rec.events if e[0] == "web.fetch.completed")
    assert done[3]["total"] == 4 and done[3]["failed"] == 1 and done[3]["unavailable"] == 1


async def test_collect_evidence_marks_changed_against_prior_snapshot_and_stale(session, mock_http):
    inc = await _incident(session)
    old = Evidence(
        incident_id=inc.id,
        type="competitor",
        status="live",
        url="https://rival.test/",
        content_hash="0" * 64,
        retrieved_at=datetime.now(UTC) - timedelta(days=3),
        raw={"block_index": [{"heading": "Enterprise SSO", "hash": "stale-hash"}]},
    )
    session.add(old)
    await session.flush()
    for host in ("acme.test", "rival.test"):
        robots(mock_http, host)
        mock_http.get(f"https://{host}/sitemap.xml").mock(return_value=httpx.Response(404))
    old_meta = '<meta property="article:modified_time" content="2019-01-01T00:00:00Z">'
    mock_http.get("https://acme.test/").mock(
        return_value=httpx.Response(
            200, headers={"content-type": "text/html"}, text=PRICING.replace("</head>", old_meta + "</head>")
        )
    )
    mock_http.get("https://rival.test/").mock(
        return_value=httpx.Response(200, text=PRICING, headers={"content-type": "text/html"})
    )
    inc.context = {"topic": "enterprise SSO", "prompts": []}
    rec = Recorder()
    async with make(mock_http) as c:
        rows = await collect_evidence(session, inc, rec, collector=c, ranker=None)
    by_url = {r.url: r for r in rows if r.url}
    assert by_url["https://acme.test/"].status == "stale"
    rival = by_url["https://rival.test/"]
    assert (
        rival.status == "changed" and rival.raw["diff"]["changed"] and rival.raw["diff"]["kind"] == "changed"
    )
    assert rival.raw["diff"]["prev_hash"] == "0" * 64


async def test_collect_evidence_never_raises_when_everything_fails_and_sync_emit_ok(session, mock_http):
    inc = await _incident(session)
    inc.context = {"topic": "x"}
    for host in ("acme.test", "rival.test"):
        mock_http.get(f"https://{host}/robots.txt").mock(side_effect=httpx.ConnectError("down"))
        mock_http.get(f"https://{host}/").mock(side_effect=httpx.ConnectError("down"))
    events = []
    async with make(mock_http) as c:
        rows = await collect_evidence(session, inc, lambda s, st, m, md: events.append((s, st)), collector=c)
    assert rows and all(
        r.status in ("failed", "unavailable") and r.content_hash is None and not r.excerpt for r in rows
    )
    completed = [e for e in events if e[0] == "web.fetch.completed"]
    assert completed and completed[0][1] == StepStatus.WARNING


async def test_collect_evidence_without_any_domains_returns_empty(session, mock_http):
    org = Organization(name="Bare", domain="", competitor_domains=[], canonical_domains=[], topics=[])
    session.add(org)
    await session.flush()
    inc = Incident(org_id=org.id, title="t", context={})
    session.add(inc)
    await session.flush()
    events = []
    rows = await collect_evidence(
        session, inc, lambda s, st, m, md: events.append(s), collector=make(mock_http)
    )
    assert rows == [] and "web.fetch.started" in events


async def test_snapshot_store_returns_none_without_prior(session, mock_http):
    inc = await _incident(session)
    assert await EvidenceRowSnapshotStore(session).latest("https://nowhere.test/") is None
    assert inc.id is not None
