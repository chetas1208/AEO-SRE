"""Profound attacks: whatever the upstream does, ingestion must never (a) fall back to fixture data, (b) write a signal
it did not receive, or (c) report success it did not achieve. HTTP is mocked with respx; payloads are synthetic."""
from __future__ import annotations

import httpx
import pytest
from app.connectors.profound import ProfoundClient
from app.domain.source_mode import SignalSourceMode, source_mode_for_signal
from app.models.core import Incident, Signal
from app.services.ingestion import ingest_org
from sqlalchemy import select

from tests.unit.test_profound_client import BASE, NOW, _org, mock_ingest_http


async def no_sleep(_):
    return None


def client(**kw) -> ProfoundClient:
    kw.setdefault("api_key", "pk_ADV_SECRET_123")
    return ProfoundClient(sleep=no_sleep, backoff_base=0.0, max_retries=1, **kw)


async def run(session, mock_http, setup, **kw):
    org = await _org(session)
    setup(mock_http)
    c = client(**kw)
    try:
        res = await ingest_org(session, org.id, client=c, now=NOW)
    finally:
        await c.aclose()
    await session.commit()
    sigs = (await session.execute(select(Signal))).scalars().all()
    incs = (await session.execute(select(Incident))).scalars().all()
    return res, sigs, incs


def everything_fails(response):
    def setup(router):
        router.route(host="api.tryprofound.com").mock(side_effect=response)

    return setup


ATTACKS = {
    "invalid_key_401": lambda r: httpx.Response(401, json={"detail": "invalid api key"}),
    "forbidden_403": lambda r: httpx.Response(403, json={"detail": "no access"}),
    "rate_limited_429": lambda r: httpx.Response(429, headers={"Retry-After": "1"}, json={"detail": "slow down"}),
    "server_500": lambda r: httpx.Response(500, text="boom"),
    "bad_gateway_502": lambda r: httpx.Response(502, text="<html>bad gateway</html>"),
    "timeout": httpx.ReadTimeout("slow"),
    "connect_error": httpx.ConnectError("refused"),
    "html_instead_of_json": lambda r: httpx.Response(200, text="<html>maintenance</html>"),
    "empty_body": lambda r: httpx.Response(200, content=b""),
    "json_null": lambda r: httpx.Response(200, json=None),
    "wrong_shape_list": lambda r: httpx.Response(200, json=[1, 2, 3]),
    "wrong_shape_dict": lambda r: httpx.Response(200, json={"unexpected": {"shape": True}}),
}


@pytest.mark.parametrize("name", sorted(ATTACKS))
async def test_hostile_upstream_creates_no_signal_no_incident_and_no_fixture_fallback(session, mock_http, name):
    res, sigs, incs = await run(session, mock_http, everything_fails(ATTACKS[name]))
    assert res.created == 0 and sigs == [], f"{name}: a signal appeared without any valid upstream data"
    assert incs == []
    assert res.status != "ok", f"{name}: ingestion reported success it did not achieve"
    assert not [s for s in sigs if source_mode_for_signal(s.source) is not SignalSourceMode.LIVE]


async def test_secret_never_appears_in_result_or_errors(session, mock_http):
    res, _, _ = await run(session, mock_http, everything_fails(ATTACKS["invalid_key_401"]))
    assert "pk_ADV_SECRET_123" not in repr(res) and "pk_ADV_SECRET_123" not in str(res.surfaces)


async def test_not_configured_makes_zero_requests_and_labels_nothing_live(session, mock_http):
    org = await _org(session)
    c = client(api_key="")
    res = await ingest_org(session, org.id, client=c, now=NOW)
    await c.aclose()
    assert res.status == "unavailable" and not mock_http.calls
    assert (await session.execute(select(Signal))).scalars().all() == []


async def test_partial_failure_keeps_only_what_was_received_and_is_not_ok(session, mock_http):
    def setup(router):
        mock_ingest_http(router)
        router.post(f"{BASE}/v2/reports/citations").respond(500, text="boom")
        router.post(f"{BASE}/v2/reports/factcheck").respond(500, text="boom")

    res, sigs, _ = await run(session, mock_http, setup)
    metrics = {s.metric for s in sigs}
    assert "visibility" in metrics, "healthy surfaces still ingest"
    assert "citation_share" not in metrics and "accuracy" not in metrics, "failed surfaces must not be invented"
    assert res.status in ("degraded", "partial", "failed"), res.status
    assert all(source_mode_for_signal(s.source) is SignalSourceMode.LIVE for s in sigs)


async def test_duplicate_delivery_is_idempotent(session, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http)
    c = client()
    first = await ingest_org(session, org.id, client=c, now=NOW)
    await session.commit()
    n = len((await session.execute(select(Signal))).scalars().all())
    again = await ingest_org(session, org.id, client=c, now=NOW)
    await session.commit()
    await c.aclose()
    assert first.created == n > 0 and again.created == 0
    assert len((await session.execute(select(Signal))).scalars().all()) == n


async def test_rows_with_missing_values_are_dropped_not_zero_filled(session, mock_http):
    def setup(router):
        mock_ingest_http(router)
        router.post(f"{BASE}/v2/reports/visibility").respond(200, json={
            "info": {"count": 2, "next_cursor": None},
            "data": [{"dimensions": ["2026-09-25"], "metrics": []},
                     {"dimensions": ["2026-09-24"], "metrics": [None, None, None]}]})

    _, sigs, _ = await run(session, mock_http, setup)
    assert all(s.value is not None for s in sigs)
    assert not [s for s in sigs if s.metric == "visibility" and s.value == 0.0]


async def test_a_live_ingest_failure_never_surfaces_fixture_labelled_rows_as_live(session, mock_http):
    """Existing dev_fixture rows stay labelled FIXTURE; a failing live ingest must not re-label or refresh them."""
    from tests import factories as f

    org = await _org(session)
    await f.make_signal(session, org, value=50.0, observed_at=NOW, source="dev_fixture")
    mock_http.route(host="api.tryprofound.com").mock(return_value=httpx.Response(500, text="boom"))
    c = client()
    await ingest_org(session, org.id, client=c, now=NOW)
    await c.aclose()
    await session.commit()
    rows = (await session.execute(select(Signal))).scalars().all()
    assert [s.source for s in rows] == ["dev_fixture"]
    assert source_mode_for_signal(rows[0].source) is SignalSourceMode.FIXTURE
