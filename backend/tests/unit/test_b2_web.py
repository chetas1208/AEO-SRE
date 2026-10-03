"""B2: SSRF extras, boilerplate-insensitive snapshots/diffs, source categories, graph provenance."""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest
from app.connectors.web.diff import diff_snapshots
from app.connectors.web.extract import extract_html, is_boilerplate_text, normalized_hash
from app.connectors.web.source import SourceCategory, classify_source
from app.connectors.web.ssrf import UnsafeURL, validate_url
from app.domain.enums import EvidenceStatus

from tests.unit.test_web_collector import PUBLIC_IP, fake_resolver, make, robots


@pytest.mark.parametrize("url", [
    "http://2130706433/", "http://0x7f.0.0.1/", "http://017700000001/", "http://[::ffff:127.0.0.1]/",
    "http://[fd00::1]/", "http://metadata.google.internal/computeMetadata/v1/", "http://100.100.100.200/",
    "http://192.0.0.192/", "https://169.254.169.254:443/", "file://localhost/etc/passwd",
])
async def test_ssrf_more_vectors_rejected(url):
    with pytest.raises(UnsafeURL):
        await validate_url(url, fake_resolver)


async def test_redirect_to_file_scheme_and_ula_ipv6_blocked(mock_http):
    robots(mock_http, "public.test")
    for target in ("file:///etc/passwd", "http://[fd12:3456::1]/"):
        mock_http.get("https://public.test/go").mock(return_value=httpx.Response(302, headers={"location": target}))
        async with make(mock_http) as c:
            res = await c.fetch("https://public.test/go")
        assert res.status == EvidenceStatus.UNAVAILABLE.value and "blocked" in res.error
    assert PUBLIC_IP  # resolver stays public for the first hop only


# ---------------------------------------------------------------- normalization / snapshots

_BODY = ("<main><h1>Enterprise SSO</h1><p>Enterprise SSO via SAML 2.0 and SCIM provisioning is included in the "
         "Enterprise plan for every customer who needs identity federation at scale.</p><h2>Pricing</h2>"
         "<p>The Enterprise plan costs $49 per seat per month, billed annually with volume discounts.</p></main>")


def _page(extra: str = "", footer: str = "All rights reserved 2026") -> str:
    return (f"<html><head><title>SSO</title></head><body><nav>Home Docs Login</nav>"
            f"<div id='cookie-banner'>We use cookies. Accept all cookies</div>{extra}{_BODY}"
            f"<div class='newsletter'>Subscribe to our newsletter</div><footer>{footer}</footer></body></html>")


def test_extraction_drops_cookie_banner_nav_footer_but_keeps_headings():
    title, text, blocks, _mod, _m = extract_html(_page())
    joined = " ".join(b.text for b in blocks)
    assert "cookies" not in joined.lower() and "newsletter" not in joined.lower() and "All rights" not in joined
    assert [b.heading for b in blocks] == ["Enterprise SSO", "Pricing"] and title == "SSO"


def test_boilerplate_text_detector_and_normalized_hash_ignores_it():
    assert is_boilerplate_text("We use cookies to improve your experience") and not is_boilerplate_text("SSO costs $49")
    _, _, b1, _, _ = extract_html(_page())
    _, _, b2, _, _ = extract_html(_page(footer="All rights reserved 2027"))
    assert normalized_hash(b1) == normalized_hash(b2)
    _, _, b3, _, _ = extract_html(_page().replace("$49", "$59"))
    assert normalized_hash(b1) != normalized_hash(b3)


def test_diff_ignores_boilerplate_only_changes_and_reports_real_section_changes():
    _, t1, b1, _, _ = extract_html(_page())
    _, t2, b2, _, _ = extract_html(_page(extra="<p>We use cookies on this website. Cookie settings</p>"))
    mk = lambda t, b: SimpleNamespace(content_hash="h" + str(len(t)), status="live", blocks=b, normalized_hash=normalized_hash(b))  # noqa: E731
    d = diff_snapshots(mk(t1, b1), mk(t2 + "x", b2))
    assert d.kind == "unchanged" and not d.changed and "boilerplate" in d.summary
    _, t3, b3, _, _ = extract_html(_page().replace("$49", "$59"))
    d2 = diff_snapshots(mk(t1, b1), mk(t3 + "yy", b3))
    assert d2.changed and [m["heading"] for m in d2.modified] == ["Pricing"]
    # evidence-row style snapshots (hash only) also honor the normalized hash
    row_a = SimpleNamespace(content_hash="a", status="live", raw={"normalized_hash": "n1", "block_index": []})
    row_b = SimpleNamespace(content_hash="b", status="live", raw={"normalized_hash": "n1", "block_index": []})
    assert diff_snapshots(row_a, row_b).kind == "unchanged"


def test_source_categories_are_constrained_and_categorical():
    own, comps = "acme.com", ["globex.com"]
    assert classify_source("https://docs.acme.com/x", own, comps) is SourceCategory.OWNED
    assert classify_source("https://www.globex.com/", own, comps) is SourceCategory.COMPETITOR
    assert classify_source("https://www.reddit.com/r/sso", own, comps) is SourceCategory.COMMUNITY
    assert classify_source("https://techcrunch.com/a", own, comps) is SourceCategory.THIRD_PARTY
    assert classify_source("", own, comps) is SourceCategory.UNKNOWN
    assert {c.value for c in SourceCategory} == {"OWNED", "COMPETITOR", "THIRD_PARTY", "COMMUNITY", "UNKNOWN"}


async def test_evidence_row_retains_source_type_hash_method_and_incident_relation(session, mock_http):
    from app.investigation.collector import collect_evidence

    from tests import factories as f

    org = await f.make_org(session)
    inc = await f.make_incident(session, org, context={"cited_urls": ["https://blog.example.org/sso"]})
    robots(mock_http, "blog.example.org")
    mock_http.get("https://blog.example.org/sso").mock(return_value=httpx.Response(
        200, headers={"content-type": "text/html"}, text=_page()))
    async with make(mock_http) as collector:
        rows = await collect_evidence(session, inc, collector=collector, max_pages=3)
    r = next(x for x in rows if x.url and "blog.example.org" in x.url)
    assert r.raw["source_category"] == "THIRD_PARTY" and r.raw["incident_relation"].startswith("cited_by_ai")
    assert r.content_hash and r.retrieved_at and r.retrieval_method and r.raw["normalized_hash"]
    assert r.raw["title_is_evidence"] is False and r.raw["has_extract"] in (True, False)


# ---------------------------------------------------------------- graph provenance


async def test_graph_edges_have_provenance_extract_hash_and_explicit_title_fallback(session):
    from app.evidence.provenance import content_hash
    from app.models.evidence import EvidenceEdge
    from app.services import pipeline
    from sqlalchemy import select

    from tests import factories as f

    org = await f.make_org(session)
    cluster = await f.make_prompt_cluster(session, org)
    inc = await f.make_incident(session, org, prompt_cluster_id=cluster.id, metrics=[{"key": "visibility", "delta": -5}])
    sym = await f.make_evidence(session, inc, type="profound", url=None, source="profound", excerpt="Visibility fell",
                                raw={"kind": "metric_change", "metric": "visibility", "delta": -5})
    page = await f.make_evidence(session, inc, type="competitor", excerpt="Globex covers SAML.",
                                 content_hash="d" * 64)
    title_only = await f.make_evidence(session, inc, type="external", excerpt="", title="Some review site",
                                       url="https://r.test/x")
    cites = await f.make_hypothesis(session, inc, [sym.id, page.id, title_only.id], rationale="x" * 30)
    bare = await f.make_hypothesis(session, inc, [], rationale="")
    inc.context = {"hypotheses_meta": {str(cites.id): {"actionable": True}, str(bare.id): {"actionable": True}}}
    await session.commit()
    await pipeline.stage_graph(session, inc.id)
    edges = list((await session.execute(select(EvidenceEdge).where(EvidenceEdge.incident_id == inc.id))).scalars())
    assert edges and all(e.provenance and e.provenance.get("retrieval_method") for e in edges)
    for e in edges:  # the stored hash covers the stored extract, not the whole page
        if e.provenance.get("extract"):
            assert e.provenance["hash"] == content_hash(e.provenance["extract"])
    by_dst = {(str(e.src_id), str(e.dst_id)): e for e in edges}
    support = by_dst[(str(page.id), str(cites.id))]
    assert support.edge_type == "supports" and support.provenance["extract_kind"] == "excerpt"
    assert support.provenance["source_hash"] == "d" * 64
    # the measured symptom is associated with a hypothesis, never SUPPORTED_BY, and a bare title is flagged as such
    assert by_dst[(str(sym.id), str(cites.id))].edge_type == "associated_with"
    tf = by_dst[(str(title_only.id), str(cites.id))]
    assert tf.edge_type == "associated_with" and tf.provenance["extract_kind"] == "title_fallback"
    # a hypothesis citing no evidence has only ASSOCIATED_WITH, and nothing is left disconnected
    into_bare = [e for e in edges if str(e.dst_id) == str(bare.id)]
    assert into_bare and {e.edge_type for e in into_bare} == {"associated_with"}
    connected = {str(e.src_id) for e in edges} | {str(e.dst_id) for e in edges}
    assert {str(sym.id), str(page.id), str(title_only.id), str(cites.id), str(bare.id)} <= connected
