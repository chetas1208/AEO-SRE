"""B2: Profound provenance, health states, runs/checkpoints, two-stage fetch, smoke, no-fixture-fallback."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
from app.connectors.profound.health import ProfoundHealth, derive_health
from app.domain.source_mode import SignalSourceMode, source_mode_for_signal
from app.services.ingestion import (
    DEEP_SURFACES,
    MONITORING_SURFACES,
    IngestConfig,
    ingest_deep,
    list_ingestion_runs,
    run_scheduled_ingest,
)
from sqlalchemy import select

from tests.unit.test_profound_client import BASE, NOW, _org, _signals, fx, make_client, mock_ingest_http


async def test_every_signal_persists_source_mode_live(session, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW)
    await session.commit()
    rows = await _signals(session, org.id)
    assert rows and res.source_mode == "LIVE"
    assert {r.raw["source_mode"] for r in rows} == {"LIVE"}
    assert all(r.raw["ingestion_run_id"] == res.run_id for r in rows)
    await client.aclose()


async def test_replay_mode_is_declared_by_caller_not_inferred_from_org(session, mock_http):
    org = await _org(session, domain="acme.com")  # name/domain say nothing about the mode
    mock_ingest_http(mock_http)
    client, _ = make_client()
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW, source_mode=SignalSourceMode.REPLAY)
    await session.commit()
    assert {r.raw["source_mode"] for r in await _signals(session, org.id)} == {"REPLAY"}
    assert res.source_mode == "REPLAY"
    await client.aclose()


def test_source_mode_prefers_persisted_value_over_source_label():
    assert source_mode_for_signal("profound", {"source_mode": "REPLAY"}) is SignalSourceMode.REPLAY
    assert source_mode_for_signal("dev_fixture") is SignalSourceMode.FIXTURE
    assert source_mode_for_signal("profound", {"_fixture": {"origin": "x"}}) is SignalSourceMode.FIXTURE
    assert source_mode_for_signal("profound") is SignalSourceMode.LIVE


async def test_live_failure_never_falls_back_to_fixtures(session, mock_http):
    from app.models.core import Signal

    org = await _org(session)
    mock_http.get(f"{BASE}/v1/org/categories").respond(500, json={"detail": "boom"})
    client, _ = make_client(max_retries=0)
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.status == "unavailable" and res.created == 0
    rows = (await session.execute(select(Signal).where(Signal.org_id == org.id))).scalars().all()
    assert rows == []  # nothing, in particular nothing labeled dev_fixture / FIXTURE
    runs = await list_ingestion_runs(session, org.id)
    assert runs[0]["source_mode"] == "LIVE" and runs[0]["status"] == "unavailable" and runs[0]["error_count"] >= 1
    await client.aclose()


async def test_unconfigured_ingest_records_run_without_signals(session, mock_http):
    org = await _org(session)
    client, _ = make_client(api_key="")
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.status == "unavailable" and not mock_http.calls
    assert await _signals(session, org.id) == []
    assert (await list_ingestion_runs(session, org.id))[0]["status"] == "unavailable"
    await client.aclose()


async def test_run_record_has_required_fields(session, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW)
    await session.commit()
    run = (await list_ingestion_runs(session, org.id))[0]
    for key in ("id", "provider", "source_mode", "started_at", "completed_at", "status", "records_seen",
                "records_normalized", "records_created", "records_updated", "error_count", "checkpoint"):
        assert key in run, key
    assert run["id"] == res.run_id and run["provider"] == "profound"
    assert run["records_created"] == res.created > 0
    await client.aclose()


async def test_scheduled_ingest_is_monitoring_only_and_deep_fetch_is_separate(session, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http)
    client, _ = make_client()
    res = await run_scheduled_ingest(session, org.id, client=client, now=NOW)
    await session.commit()
    assert res.stage == "monitoring" and set(res.surfaces) == set(MONITORING_SURFACES)
    kinds = {r.kind for r in await _signals(session, org.id)}
    assert "query_fanout" not in kinds and not any(r.kind == "factcheck" for r in await _signals(session, org.id))
    fanout_calls = [c for c in mock_http.calls if str(c.request.url).endswith("/query-fanouts")]
    assert fanout_calls == []
    deep = await ingest_deep(session, org.id, client=client, now=NOW)
    await session.commit()
    assert deep.stage == "deep" and set(deep.surfaces) == set(DEEP_SURFACES)
    assert "query_fanout" in {r.kind for r in await _signals(session, org.id)}
    await client.aclose()


async def test_failed_surface_keeps_old_checkpoint_and_next_run_resumes(session, mock_http):
    org = await _org(session)
    mock_ingest_http(mock_http, factcheck_status=403)
    client, _ = make_client()
    cfg = IngestConfig(pull=("visibility", "citations", "factcheck"))
    r1 = await _run(session, org.id, client, cfg, NOW)
    assert r1.checkpoints.get("visibility") == "2026-09-25" and "factcheck" not in r1.checkpoints
    # a day later: healthy surfaces re-pull only the restate window, the failed one re-pulls its gap
    mock_http.reset()
    mock_ingest_http(mock_http)
    seen: list[str] = []

    def spy(request: httpx.Request):
        body = json.loads(request.content)
        seen.append(f"{body.get('start_date')}")
        return httpx.Response(200, json={"info": {"count": 0, "next_cursor": None}, "data": []})

    mock_http.post(f"{BASE}/v2/reports/factcheck").mock(side_effect=spy)
    r2 = await _run(session, org.id, client, cfg, NOW + timedelta(days=1))
    assert r2.window[0] == "2026-09-06" and min(seen) == "2026-09-06"  # window start reaches back for the gap
    assert (await list_ingestion_runs(session, org.id))[0]["checkpoint"]["factcheck"] == r2.window[1]
    await client.aclose()


async def _run(session, org_id, client, cfg, now):
    from app.services.ingestion import ingest_org

    res = await ingest_org(session, org_id, client=client, config=cfg, now=now)
    await session.commit()
    return res


def test_health_states():
    ok = {"visibility": {"state": "healthy", "reason": None}}
    assert derive_health(False, {}) is ProfoundHealth.NOT_CONFIGURED
    assert derive_health(True, ok) is ProfoundHealth.CONNECTED
    assert derive_health(True, {}) is ProfoundHealth.DEGRADED  # configured but unproven is never CONNECTED
    assert derive_health(True, {"a": {"state": "unavailable", "reason": "categories:auth_failed"}}) is ProfoundHealth.AUTH_FAILED
    assert derive_health(True, {"a": {"state": "degraded", "reason": "rate_limited"}}) is ProfoundHealth.RATE_LIMITED
    assert derive_health(True, {**ok, "b": {"state": "degraded", "reason": "error:X"}}) is ProfoundHealth.DEGRADED
    assert derive_health(True, {**ok, "agents": {"state": "unavailable", "reason": "x"}}) is ProfoundHealth.CONNECTED


def test_untracked_competitors_is_account_config_not_connector_degradation():
    ok = {"a": {"state": "healthy", "reason": None}}
    gap = {"state": "degraded", "reason": "no_competitor_assets_tracked"}
    assert derive_health(True, {**ok, "competitors": gap}) is ProfoundHealth.CONNECTED
    # any other degradation of the same surface still degrades the connector
    assert derive_health(True, {**ok, "competitors": {"state": "degraded", "reason": "error:X"}}) is ProfoundHealth.DEGRADED


def test_same_ignores_float_noise_but_not_real_change():
    from app.services.ingestion import _same

    assert _same({"s": 0.027290998304573516, "d": "x"}, {"s": 0.02729099830457351, "d": "x"})
    assert not _same({"s": 0.0273}, {"s": 0.0274})
    assert not _same({"a": 1}, {"a": 1, "b": 2})
    assert _same(1, 1.0) and not _same(True, 1)


def test_signal_source_label_is_capped_at_column_width():
    from app.connectors.profound.normalize import NormalizedSignal

    sig = NormalizedSignal(kind="visibility", metric="m", value=1.0, observed_at=datetime.now(UTC),
                           segment="prompt=" + "x" * 300)
    assert len(sig.source) == 128


async def test_capabilities_report_not_configured_without_noise(session, monkeypatch):
    from app.core.config import get_settings
    from app.services.capabilities import check_profound

    monkeypatch.setattr(get_settings(), "profound_api_key", "")
    cap = await check_profound(session)
    assert cap.meta["health_state"] == "NOT_CONFIGURED"


async def test_smoke_unconfigured_and_redacts(mock_http):
    from app.devtools.profound_smoke import smoke

    client, _ = make_client(api_key="")
    report, code = await smoke(client)
    assert code == 2 and report["health"] == "NOT_CONFIGURED" and not mock_http.calls
    await client.aclose()


async def test_smoke_connected_minimal_makes_one_read_request(mock_http):
    from app.devtools.profound_smoke import smoke

    mock_http.get(f"{BASE}/v1/org/categories").respond(200, json=fx("categories.json"))
    client, _ = make_client()
    report, code = await smoke(client, minimal=True)
    assert code == 0 and report["health"] == "CONNECTED" and report["request"]["normalized_categories"] >= 1
    assert len(mock_http.calls) == 1 and mock_http.calls[0].request.method == "GET"
    assert "pk_test_SECRET_VALUE" not in json.dumps(report)
    await client.aclose()


async def test_smoke_auth_failure_and_rate_limit_exit_codes(mock_http):
    from app.devtools.profound_smoke import smoke

    mock_http.get(f"{BASE}/v1/org/categories").respond(401, json={"detail": "bad key"})
    client, _ = make_client()
    report, code = await smoke(client)
    assert code == 3 and report["health"] == "AUTH_FAILED"
    mock_http.reset()
    mock_http.get(f"{BASE}/v1/org/categories").respond(429, headers={"Retry-After": "9999"}, json={})
    report, code = await smoke(client)
    assert code == 4 and report["health"] == "RATE_LIMITED"
    await client.aclose()


def test_ingest_live_uses_scheduler_service():
    from app.devtools import ingest_live
    from app.services import pipeline

    src = open(ingest_live.__file__).read()
    assert "run_scheduled_ingest" in src
    import inspect

    assert "run_scheduled_ingest" in inspect.getsource(pipeline.ingest)
    assert NOW < datetime.now(UTC) + timedelta(days=3650)
