"""Profound connector tests: client (auth/retry/rate-limit/errors), request validation, capabilities,
normalizers and ingestion. HTTP is mocked with respx. Fixtures under tests/fixtures/profound/ are SYNTHETIC (built from
the published OpenAPI spec, labeled `_fixture`), NOT recorded live responses - no Profound API key was available."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from app.connectors.profound import (
    CitationsQuery,
    FactCheckQuery,
    InMemoryRawPayloadStore,
    ProfoundAuthError,
    ProfoundClient,
    ProfoundNotConfigured,
    ProfoundNotFound,
    ProfoundPermissionError,
    ProfoundRateLimited,
    ProfoundResponseError,
    ProfoundServerError,
    ProfoundTransportError,
    ProfoundValidationError,
    QueryFanoutsQuery,
    VisibilityQuery,
    VolumeQuery,
    normalize_citations,
    normalize_factcheck,
    normalize_fanouts,
    normalize_prompts,
    normalize_visibility,
    normalize_volume,
)
from app.connectors.profound.client import parse_retry_after
from app.domain.enums import CapabilityState
from pydantic import ValidationError
from sqlalchemy import select

FX = Path(__file__).resolve().parent.parent / "fixtures" / "profound"
BASE = "https://api.tryprofound.com"
KEY = "pk_test_SECRET_VALUE"


def fx(name: str):
    d = json.loads((FX / name).read_text())
    return d.get("_list", d)


class Sleeper:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def make_client(**kw) -> tuple[ProfoundClient, Sleeper]:
    sl = Sleeper()
    kw.setdefault("api_key", KEY)
    return ProfoundClient(sleep=sl, backoff_base=0.1, **kw), sl


def vq(**kw) -> VisibilityQuery:
    return VisibilityQuery(category_id="c1", start_date="2026-09-20", end_date="2026-09-25", **kw)


# ---------------------------------------------------------------- client: auth / config


async def test_not_configured_raises_and_makes_no_request(mock_http):
    c, _ = make_client(api_key="")
    with pytest.raises(ProfoundNotConfigured):
        await c.list_categories()
    assert not mock_http.calls
    await c.aclose()


async def test_sends_api_key_header_and_never_leaks_it(mock_http):
    route = mock_http.get(f"{BASE}/v1/org/categories").respond(200, json=fx("categories.json"))
    c, _ = make_client()
    r = await c.list_categories()
    assert route.calls.last.request.headers["x-api-key"] == KEY
    assert KEY not in repr(c) and KEY not in str(r.endpoint)
    await c.aclose()


async def test_base_url_override(mock_http):
    route = mock_http.get("https://example.test/v1/org/models").respond(200, json=[])
    c, _ = make_client(base_url="https://example.test/")
    await c.list_models()
    assert route.called
    await c.aclose()


# ---------------------------------------------------------------- client: error mapping


@pytest.mark.parametrize(
    "status,exc",
    [(401, ProfoundAuthError), (403, ProfoundPermissionError), (404, ProfoundNotFound), (422, ProfoundValidationError),
     (400, ProfoundValidationError)],
)
async def test_error_mapping_no_retry(mock_http, status, exc):
    route = mock_http.get(f"{BASE}/v1/org/models").respond(status, json=fx("error_422.json"))
    c, sl = make_client()
    with pytest.raises(exc) as ei:
        await c.list_models()
    assert ei.value.status == status and ei.value.detail
    assert route.call_count == 1 and sl.calls == []
    assert KEY not in str(ei.value)
    await c.aclose()


async def test_429_honours_retry_after_then_succeeds(mock_http):
    route = mock_http.get(f"{BASE}/v1/org/models").mock(
        side_effect=[httpx.Response(429, headers={"Retry-After": "7"}), httpx.Response(200, json=[])]
    )
    c, sl = make_client()
    r = await c.list_models()
    assert r.status == 200 and route.call_count == 2 and sl.calls == [7.0]
    await c.aclose()


async def test_429_retry_after_too_long_degrades_without_sleeping(mock_http):
    mock_http.get(f"{BASE}/v1/org/models").respond(429, headers={"Retry-After": "3600"})
    c, sl = make_client()
    with pytest.raises(ProfoundRateLimited) as ei:
        await c.list_models()
    assert ei.value.retry_after == 3600 and sl.calls == []
    await c.aclose()


async def test_429_exhausted(mock_http):
    route = mock_http.get(f"{BASE}/v1/org/models").respond(429, headers={"Retry-After": "1"})
    c, sl = make_client(max_retries=2)
    with pytest.raises(ProfoundRateLimited):
        await c.list_models()
    assert route.call_count == 3 and len(sl.calls) == 2
    await c.aclose()


def test_parse_retry_after_variants():
    assert parse_retry_after(httpx.Headers({"retry-after": "5"})) == 5
    assert parse_retry_after(httpx.Headers({"x-ratelimit-reset": "1030"}), now=lambda: 1000.0) == 30
    assert parse_retry_after(httpx.Headers({})) is None


async def test_5xx_retried_with_backoff_then_success(mock_http):
    route = mock_http.get(f"{BASE}/v1/org/models").mock(
        side_effect=[httpx.Response(503), httpx.Response(502), httpx.Response(200, json=[])]
    )
    c, sl = make_client()
    await c.list_models()
    assert route.call_count == 3 and len(sl.calls) == 2 and all(0 < s <= 20 for s in sl.calls)
    await c.aclose()


async def test_5xx_exhausted_maps_to_server_error(mock_http):
    mock_http.get(f"{BASE}/v1/org/models").respond(500)
    c, _ = make_client(max_retries=1)
    with pytest.raises(ProfoundServerError):
        await c.list_models()
    await c.aclose()


async def test_timeout_retried_then_transport_error(mock_http):
    route = mock_http.get(f"{BASE}/v1/org/models").mock(side_effect=httpx.ConnectTimeout("t"))
    c, _ = make_client(max_retries=2)
    with pytest.raises(ProfoundTransportError):
        await c.list_models()
    assert route.call_count == 3
    await c.aclose()


async def test_non_json_success_is_response_error(mock_http):
    mock_http.get(f"{BASE}/v1/org/models").respond(200, text="<html>")
    c, _ = make_client()
    with pytest.raises(ProfoundResponseError):
        await c.list_models()
    await c.aclose()


async def test_rate_limit_headers_tracked(mock_http):
    mock_http.get(f"{BASE}/v1/org/models").respond(
        200, json=[], headers={"X-RateLimit-Limit": "600", "X-RateLimit-Remaining": "598", "X-RateLimit-Reset": "99"}
    )
    c, _ = make_client()
    r = await c.list_models()
    assert r.rate_limit == {"limit": 600, "remaining": 598, "reset": 99}
    await c.aclose()


# ---------------------------------------------------------------- raw payloads / pagination


async def test_raw_store_gets_unchanged_payload_and_ref_is_stable(mock_http):
    payload = fx("visibility_headline.json")
    mock_http.post(f"{BASE}/v2/reports/visibility").respond(200, json=payload)
    store = InMemoryRawPayloadStore()
    c, _ = make_client(raw_store=store)
    r1 = await c.visibility(vq(metrics=["visibility_score"], limit=50))
    r2 = await c.visibility(vq(metrics=["visibility_score"], limit=50))
    assert r1.raw_ref and r1.raw_ref == r2.raw_ref and r1.raw_ref.startswith("profound://")
    saved = await store.load(r1.raw_ref)
    assert saved["payload"] == payload and KEY not in json.dumps(saved)
    assert saved["endpoint"] == "POST /v2/reports/visibility"
    await c.aclose()


async def test_raw_store_failure_does_not_break_read(mock_http):
    class Boom:
        async def save(self, **kw):
            raise RuntimeError("disk full")

        async def load(self, ref):
            return None

    mock_http.get(f"{BASE}/v1/org/models").respond(200, json=[])
    c, _ = make_client(raw_store=Boom())
    r = await c.list_models()
    assert r.raw_ref is None
    await c.aclose()


async def test_cursor_pagination_follows_next_cursor(mock_http):
    seen = []

    def handler(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(body.get("cursor"))
        nxt = {None: "c2", "c2": "c3", "c3": None}[body.get("cursor")]
        return httpx.Response(200, json={"info": {"count": 1, "next_cursor": nxt}, "data": [{"date": "2026-09-20"}]})

    mock_http.post(f"{BASE}/v2/reports/visibility").mock(side_effect=handler)
    c, _ = make_client()
    pages = [r async for r in c.iter_visibility(vq(limit=50))]
    assert len(pages) == 3 and seen == [None, "c2", "c3"]
    capped = [r async for r in c.iter_visibility(vq(limit=50), max_pages=2)]
    assert len(capped) == 2
    await c.aclose()


# ---------------------------------------------------------------- request validation


def test_request_validation():
    with pytest.raises(ValidationError):
        VisibilityQuery(category_id="c", start_date="2026/09/01", end_date="2026-09-02")
    with pytest.raises(ValidationError):
        VisibilityQuery(category_id="c", start_date="2026-09-03", end_date="2026-09-02")
    with pytest.raises(ValidationError):
        vq(limit=51)
    with pytest.raises(ValidationError):
        vq(group_by=["bogus"])
    with pytest.raises(ValidationError):
        vq(metrics=["mentions_count"])  # a v1-only metric
    with pytest.raises(ValidationError):
        vq(nonsense=1)
    with pytest.raises(ValidationError):
        VolumeQuery(keyword="", start_date="2026-09-01", end_date="2026-09-02")
    with pytest.raises(ValidationError):
        VolumeQuery(keyword="x", start_date="2026-09-03", end_date="2026-09-02", platforms=["bing.com"])


def test_payload_drops_unset_fields():
    p = vq(metrics=["visibility_score"], group_by=["date"], limit=50).payload()
    assert p == {"category_id": "c1", "start_date": "2026-09-20", "end_date": "2026-09-25",
                 "metrics": ["visibility_score"], "group_by": ["date"], "interval": "day", "scope": "owned", "limit": 50}


def test_request_models_match_published_openapi_subset():
    spec = json.loads((FX / "openapi_subset.json").read_text())
    pairs = [
        (VisibilityQuery, "POST /v2/reports/visibility"),
        (CitationsQuery, "POST /v2/reports/citations"),
        (FactCheckQuery, "POST /v2/reports/factcheck"),
        (QueryFanoutsQuery, "POST /v2/reports/query-fanouts"),
        (VolumeQuery, "POST /v2/prompt-volumes/volume/on-the-fly"),
    ]
    for model, key in pairs:
        s = spec[key]
        fields = {n for n in model.model_fields}
        assert fields <= set(s["request"]), (key, fields - set(s["request"]))
        assert set(s["required"]) <= fields, (key, set(s["required"]) - fields)
    vis = spec["POST /v2/reports/visibility"]["request"]
    assert set(vis["group_by"]["enum"]) == {"date", "model", "topic", "region", "prompt", "persona"}
    assert set(vis["metrics"]["enum"]) == {"visibility_score", "share_of_voice", "average_position"}
    assert set(spec["POST /v2/reports/citations"]["request"]["metrics"]["enum"]) >= {"citation_share", "count"}


# ---------------------------------------------------------------- capabilities


def mock_all_surfaces(router, *, factcheck=200, volume=200, assets="category_assets.json"):
    router.get(f"{BASE}/v1/org/categories").respond(200, json=fx("categories.json"))
    router.post(f"{BASE}/v2/reports/visibility").respond(200, json=fx("visibility_headline.json"))
    router.post(f"{BASE}/v2/reports/citations").respond(200, json=fx("citations_headline.json"))
    router.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/prompts").respond(200, json=fx("prompts.json"))
    router.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/assets").respond(200, json=fx(assets))
    router.post(f"{BASE}/v2/reports/factcheck").respond(factcheck, json=fx("factcheck_headline.json") if factcheck == 200 else {})
    router.post(f"{BASE}/v2/reports/query-fanouts").respond(200, json=fx("fanouts.json"))
    router.post(f"{BASE}/v2/prompt-volumes/volume/on-the-fly").respond(volume, json=fx("volume_enterprise_sso.json") if volume == 200 else {},
                                                                       headers={"Retry-After": "3600"} if volume == 429 else {})
    router.get(f"{BASE}/v1/agents").respond(200, json=fx("agents.json"))


async def test_capabilities_unconfigured_is_unavailable_and_offline(mock_http):
    c, _ = make_client(api_key="")
    caps = await c.capabilities()
    assert set(caps) == {"visibility", "citations", "prompts", "prompt_volume", "competitors", "factcheck", "query_fanouts", "agents"}
    assert set(caps.values()) == {CapabilityState.UNAVAILABLE}
    assert not mock_http.calls
    rep = await c.capability_report()
    assert all(v.verification == "unverified" and v.reason == "not_configured" for v in rep.values())
    await c.aclose()


async def test_capabilities_all_healthy_live_probe(mock_http):
    mock_all_surfaces(mock_http)
    c, _ = make_client(api_key="healthy-key")
    caps = await c.capabilities(force=True)
    assert set(caps.values()) == {CapabilityState.HEALTHY}
    rep = await c.capability_report()
    assert all(v.verification == "live_verified" for v in rep.values())
    # second call is served from the shared TTL cache: no extra requests
    n = len(mock_http.calls)
    await ProfoundClient(api_key="healthy-key").capabilities()
    assert len(mock_http.calls) == n
    await c.aclose()


async def test_capabilities_per_surface_degradation(mock_http):
    assets = json.loads((FX / "category_assets.json").read_text())["_list"]
    only_owned = [a for a in assets if a["is_owned"]]
    mock_all_surfaces(mock_http, factcheck=403, volume=429)
    mock_http.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/assets").respond(200, json=only_owned)
    c, _ = make_client(api_key="degraded-key", max_retries=0)
    caps = await c.capabilities(force=True)
    assert caps["factcheck"] == CapabilityState.UNAVAILABLE
    assert caps["prompt_volume"] == CapabilityState.DEGRADED
    assert caps["competitors"] == CapabilityState.DEGRADED
    assert caps["visibility"] == CapabilityState.HEALTHY
    rep = await c.capability_report()
    assert rep["factcheck"].reason == "forbidden_or_not_enabled" and rep["factcheck"].verification == "unverified"
    assert rep["competitors"].reason == "no_competitor_assets_tracked"
    await c.aclose()


async def test_capabilities_never_raises_on_total_failure(mock_http):
    mock_http.get(f"{BASE}/v1/org/categories").mock(side_effect=httpx.ConnectError("down"))
    c, _ = make_client(api_key="down-key", max_retries=0)
    caps = await c.capabilities(force=True)
    assert set(caps.values()) == {CapabilityState.DEGRADED}
    await c.aclose()


async def test_capabilities_bad_key_is_unavailable(mock_http):
    mock_http.get(f"{BASE}/v1/org/categories").respond(401, json={"detail": "Invalid API key"})
    c, _ = make_client(api_key="bad-key")
    assert set((await c.capabilities(force=True)).values()) == {CapabilityState.UNAVAILABLE}
    await c.aclose()


# ---------------------------------------------------------------- normalizers


def test_normalize_visibility_owned_and_competitor():
    sigs = normalize_visibility(fx("visibility_headline.json"), raw_ref="profound://x")
    vis = [s for s in sigs if s.metric == "visibility"]
    assert len(vis) == 6 and vis[0].value == 0.60 and vis[-1].value == 0.37
    assert vis[0].observed_at == datetime(2026, 9, 20, 4, 0, tzinfo=UTC)  # ET midnight (EDT) in UTC
    assert {s.metric for s in sigs} == {"visibility", "share_of_voice", "avg_position"}
    assert all(s.raw_ref == "profound://x" and s.source == "profound" and s.segment is None for s in sigs)
    comp = normalize_visibility(fx("visibility_competitors_headline.json"))
    assert {s.competitor for s in comp} == {"Globex", "Initech"} and {s.metric for s in comp} == {"competitor_share"}
    assert next(s for s in comp if s.competitor == "Globex" and s.observed_at.day == 25).value == pytest.approx(0.54)


def test_normalize_visibility_segments_get_distinct_series_sources():
    by_model = normalize_visibility(fx("visibility_by_model.json"))
    assert {s.engine for s in by_model} == {"ChatGPT"} and {s.source for s in by_model} == {"profound:model=ChatGPT"}
    by_topic = normalize_visibility(fx("visibility_by_topic.json"))
    assert {s.cluster for s in by_topic} == {"Enterprise SSO", "Pricing"} and {s.source for s in by_topic} == {"profound"}


def test_normalize_drops_rows_without_values_never_defaults():
    payload = {"data": [{"asset": {"name": "A", "owned": True}, "date": "2026-09-20", "visibility_score": None,
                         "share_of_voice": "n/a", "average_position": float("nan")},
                        "garbage", {"date": "2026-09-21", "visibility_score": 0.5}]}
    sigs = normalize_visibility(payload)
    assert [(s.metric, s.value) for s in sigs] == [("visibility", 0.5)]
    assert normalize_visibility({"nope": 1}) == [] and normalize_visibility(None) == []


def test_normalize_citations_factcheck_volume():
    cit = normalize_citations(fx("citations_headline.json"))
    assert {s.metric for s in cit} == {"citation_share", "citation_count"}
    assert all(s.source == "profound:domain=acme.com" for s in cit)
    fc = normalize_factcheck(fx("factcheck_headline.json"))
    assert {s.metric for s in fc} == {"accuracy", "factual_conflicts"}
    assert [s.value for s in fc if s.metric == "factual_conflicts"][-1] == 20 - int(0.65 * 20)
    vol = normalize_volume(fx("volume_enterprise_sso.json"), keyword="Enterprise SSO", cluster="Enterprise SSO")
    weekly = [s for s in vol if s.source == "profound"]
    assert [s.value for s in weekly] == [1500.0, 1500.0]  # 1200+300 on 09-07, 1500 on 09-14
    assert [s.source for s in vol if s.source != "profound"] == ["profound:volume_monthly"]


def test_normalize_prompts():
    ps = normalize_prompts(fx("prompts.json"))
    assert [p.id for p in ps] == ["p1", "p2", "p3"] and ps[0].topic == "Enterprise SSO" and ps[0].personas == ["CISO"]


def test_normalize_fanouts_keeps_query_and_share():
    sigs = normalize_fanouts(fx("fanouts.json"), raw_ref="profound://fanouts")
    assert {s.metric for s in sigs} == {"fanout_share"}
    assert all(s.kind == "query_fanout" and s.extra["query"] for s in sigs)
    moved = [s for s in sigs if s.extra["query"] == "enterprise analytics SSO"]
    assert [s.value for s in moved] == [0.42, 0.18]


# ---------------------------------------------------------------- ingestion


def _visibility_handler(request: httpx.Request):
    body = json.loads(request.content)
    gb = body.get("group_by") or []
    if body.get("scope") == "all":
        return httpx.Response(200, json=fx("visibility_competitors_headline.json"))
    if gb == ["date", "topic"]:
        return httpx.Response(200, json=fx("visibility_by_topic.json"))
    if gb == ["date", "model"]:
        return httpx.Response(200, json=fx("visibility_by_model.json"))
    if gb == ["date"]:
        return httpx.Response(200, json=fx("visibility_headline.json"))
    return httpx.Response(200, json={"info": {"count": 0, "next_cursor": None}, "data": []})  # region/persona: no rows


def mock_ingest_http(router, *, factcheck_status=200):
    router.get(f"{BASE}/v1/org/categories").respond(200, json=fx("categories.json"))
    router.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/assets").respond(200, json=fx("category_assets.json"))
    router.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/topics").respond(200, json=fx("topics.json"))
    router.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/prompts").respond(200, json=fx("prompts.json"))
    router.post(f"{BASE}/v2/reports/visibility").mock(side_effect=_visibility_handler)
    router.post(f"{BASE}/v2/reports/citations").respond(200, json=fx("citations_headline.json"))
    if factcheck_status == 200:
        router.post(f"{BASE}/v2/reports/factcheck").respond(200, json=fx("factcheck_headline.json"))
    else:
        router.post(f"{BASE}/v2/reports/factcheck").respond(factcheck_status, json={"detail": "FactCheck not enabled"})
    router.post(f"{BASE}/v2/reports/query-fanouts").respond(200, json=fx("fanouts.json"))
    router.post(f"{BASE}/v2/prompt-volumes/volume/on-the-fly").respond(200, json=fx("volume_enterprise_sso.json"))


async def _org(session, domain="acme.com"):
    from app.models.core import Organization

    org = Organization(name="Acme", domain=domain)
    session.add(org)
    await session.commit()
    return org


NOW = datetime(2026, 9, 26, 15, 0, tzinfo=UTC)


async def _signals(session, org_id):
    from app.models.core import Signal

    return (await session.execute(select(Signal).where(Signal.org_id == org_id))).scalars().all()


async def test_ingest_unconfigured_writes_nothing_and_reports_unavailable(session, mock_http):
    from app.models.core import Setting
    from app.services.ingestion import ingest_org

    org = await _org(session)
    client, _ = make_client(api_key="")
    res = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.status == "unavailable" and res.created == 0
    assert await _signals(session, org.id) == []
    assert not mock_http.calls
    cap = await session.get(Setting, f"profound.capabilities.{org.id}")
    assert {v["state"] for v in cap.value["surfaces"].values()} == {"unavailable"}
    assert (await session.get(Setting, f"profound.last_sync.{org.id}")).value["success"] is None
    await client.aclose()


async def test_ingest_writes_signals_idempotently(session, mock_http):
    from app.models.core import PromptCluster, Setting
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http)
    store = InMemoryRawPayloadStore()
    client, _ = make_client(raw_store=store)
    res = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.status == "ok", res.surfaces
    assert res.category == {"id": "11111111-1111-4111-8111-111111111111", "name": "Enterprise Identity"}
    assert res.window == ("2026-09-05", "2026-09-25")
    rows = await _signals(session, org.id)
    assert res.created == len(rows) > 0 and res.updated == 0
    metrics = {r.metric for r in rows}
    assert {"visibility", "avg_position", "competitor_share", "citation_share", "accuracy", "prompt_volume",
            "factual_conflicts"} <= metrics
    assert all(r.source.startswith("profound") and r.raw_payload_ref and r.raw["dedupe_key"] for r in rows)
    for r in rows:
        assert await store.load(r.raw_payload_ref)
    comp = [r for r in rows if r.metric == "competitor_share"]
    assert {r.raw["competitor"] for r in comp} == {"Globex", "Initech"}
    clusters = (await session.execute(select(PromptCluster).where(PromptCluster.org_id == org.id))).scalars().all()
    by_topic = {c.topic: c for c in clusters}
    assert {"Enterprise SSO", "Pricing"} <= set(by_topic) and len(by_topic["Enterprise SSO"].prompts) == 2
    assert any(r.prompt_cluster_id == by_topic["Enterprise SSO"].id for r in rows)
    # no baseline is invented
    assert all(r.baseline is None for r in rows)

    # re-ingest: nothing new, nothing duplicated
    mock_http.calls.reset()
    res2 = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res2.created == 0 and res2.updated == 0 and res2.unchanged >= len(rows)
    assert len(await _signals(session, org.id)) == len(rows)

    sync = (await session.get(Setting, f"profound.last_sync.{org.id}")).value
    assert sync["success"]["at"] == NOW.isoformat() and sync["status"] == "ok"
    await client.aclose()


async def test_ingest_restated_value_updates_in_place(session, mock_http):
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    n = len(await _signals(session, org.id))
    restated = fx("citations_headline.json")
    restated["data"][0]["citation_share"] = 0.33
    mock_http.post(f"{BASE}/v2/reports/citations").respond(200, json=restated)
    res = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    rows = await _signals(session, org.id)
    assert len(rows) == n and res.updated == 2 and res.created == 0  # citation_share + count share the restated row
    assert any(r.metric == "citation_share" and r.value == 0.33 for r in rows)
    await client.aclose()


async def test_ingest_degrades_per_surface_and_continues(session, mock_http):
    from app.models.core import Setting
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http, factcheck_status=403)
    client, _ = make_client()
    res = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.status == "degraded"
    assert res.surfaces["factcheck"]["state"] == "unavailable"
    assert res.surfaces["factcheck"]["reason"] == "date:forbidden_or_not_enabled; date+topic:forbidden_or_not_enabled" or \
        "forbidden_or_not_enabled" in res.surfaces["factcheck"]["reason"]
    assert res.surfaces["visibility"]["state"] == "healthy" and res.surfaces["citations"]["state"] == "healthy"
    rows = await _signals(session, org.id)
    assert rows and not any(r.kind == "factcheck" for r in rows)
    cap = (await session.get(Setting, f"profound.capabilities.{org.id}")).value
    assert cap["surfaces"]["factcheck"]["verification"] == "unverified"
    await client.aclose()


async def test_ingest_keeps_partial_data_when_a_later_page_fails(session, mock_http):
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http)
    calls = {"n": 0}

    def flaky(request: httpx.Request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json={**fx("citations_headline.json"), "info": {"count": 6, "next_cursor": "c2"}})
        return httpx.Response(500)

    mock_http.post(f"{BASE}/v2/reports/citations").mock(side_effect=flaky)
    client, _ = make_client(max_retries=0)
    res = await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.surfaces["citations"]["state"] == "degraded" and res.surfaces["citations"]["rows"] > 0
    assert any(r.kind == "citation" for r in await _signals(session, org.id))
    await client.aclose()


async def test_ingest_unknown_domain_has_no_matching_category(session, mock_http):
    from app.services.ingestion import ingest_org

    org = await _org(session, domain="other-company.com")
    mock_http.get(f"{BASE}/v1/org/categories").respond(200, json=fx("categories.json"))
    mock_http.get(url__regex=rf"{BASE}/v1/org/categories/[^/]+/assets").respond(200, json=fx("category_assets.json"))
    client, _ = make_client()
    res = await ingest_org(session, org.id, client=client, now=NOW)
    assert res.status == "unavailable" and res.created == 0
    assert "no_profound_category_owns_other-company.com" in res.surfaces["visibility"]["reason"]
    assert await _signals(session, org.id) == []
    await client.aclose()


async def test_ingest_auth_failure_is_unavailable_not_exception(session, mock_http):
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_http.get(f"{BASE}/v1/org/categories").respond(401, json={"detail": "Invalid API key"})
    client, _ = make_client()
    res = await ingest_org(session, org.id, client=client, now=NOW)
    assert res.status == "unavailable" and res.surfaces["visibility"]["reason"] == "category_discovery:auth_failed"
    await client.aclose()


async def test_ingest_unknown_org_returns_failed(session):
    from app.services.ingestion import ingest_org

    res = await ingest_org(session, uuid.uuid4())
    assert res.status == "failed" and res.error == "organization_not_found"


async def test_ingest_fills_empty_domain_lists_but_never_overwrites(session, mock_http):
    from app.services.ingestion import ingest_org

    org = await _org(session)
    org.competitor_domains = ["keepme.com"]
    await session.commit()
    mock_ingest_http(mock_http)
    client, _ = make_client()
    await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    await session.refresh(org)
    assert org.competitor_domains == ["keepme.com"]
    assert org.canonical_domains == ["acme.com", "acme.io"]
    await client.aclose()


async def test_ingest_is_read_only_against_profound(session, mock_http):
    """Only report/list reads: no agent runs, prompt creation or other mutating Profound endpoints."""
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    await ingest_org(session, org.id, client=client, now=NOW)
    for call in mock_http.calls:
        path = call.request.url.path
        assert call.request.method == "GET" or "/reports/" in path or "/prompt-volumes/" in path, (call.request.method, path)
        assert "/runs" not in path and "/publish" not in path
    await client.aclose()


async def test_ingested_signals_are_consumable_by_the_detector(session, mock_http):
    """Contract check with app.incidents.detector: synthetic visibility drop in the fixture -> an incident."""
    detector = pytest.importorskip("app.incidents.detector")
    from app.services.ingestion import ingest_org

    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    await ingest_org(session, org.id, client=client, now=NOW)
    await session.commit()
    created = await detector.detect_incidents(session, org.id, now=NOW)
    assert created, "detector found nothing in the ingested drop"
    assert any(i.category == "visibility_drop" for i in created)
    await client.aclose()
