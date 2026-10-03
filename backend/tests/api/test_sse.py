"""SSE contract (UI.md section 54): chronological events, id = seq, Last-Event-ID replay, heartbeat, no fabrication."""
from __future__ import annotations

import asyncio
import os

import pytest
from app.core.events import EventBus, get_event_bus
from app.domain.enums import StepStatus

from tests import factories as f
from tests.helpers import asgi_stream


@pytest.fixture
async def inc(session, org):
    return await f.make_incident(session, org)


async def _emit_n(incident_id, n, stage="crawl"):
    bus = get_event_bus()
    return [await bus.emit(None, incident_id, f"{stage}_{i}", StepStatus.SUCCESS, f"step {i}", {"i": i})
            for i in range(n)]


def _events(frames):
    return [fr for fr in frames if "json" in fr]


async def test_stream_replays_persisted_events_in_order(fastapi_app, inc):
    await _emit_n(inc.id, 3)
    status, headers, frames, _ = await asgi_stream(
        fastapi_app, f"/api/incidents/{inc.id}/events", until=lambda fr: len(_events(fr)) >= 3)
    assert status == 200
    assert headers["content-type"].startswith("text/event-stream")
    evs = _events(frames)
    assert [e["json"]["stage"] for e in evs] == ["crawl_0", "crawl_1", "crawl_2"]
    seqs = [int(e["id"]) for e in evs]
    assert seqs == sorted(seqs) and len(set(seqs)) == 3
    body = evs[0]["json"]
    for key in ("id", "incident_id", "timestamp", "stage", "status", "message", "metadata"):
        assert key in body, f"SSE payload missing {key} (UI.md section 54)"
    assert body["incident_id"] == str(inc.id)
    assert body["metadata"] == {"i": 0}


async def test_last_event_id_header_replays_only_newer(fastapi_app, inc):
    await _emit_n(inc.id, 4)
    _, _, frames, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events",
                                        until=lambda fr: len(_events(fr)) >= 4)
    ids = [int(e["id"]) for e in _events(frames)]
    _, _, resumed, _ = await asgi_stream(
        fastapi_app, f"/api/incidents/{inc.id}/events", headers={"Last-Event-ID": str(ids[1])},
        until=lambda fr: len(_events(fr)) >= 2)
    got = [int(e["id"]) for e in _events(resumed)]
    assert got == ids[2:], "reconnect must resume strictly after Last-Event-ID, without duplicates or gaps"


async def test_after_seq_query_param_equivalent(fastapi_app, inc):
    await _emit_n(inc.id, 3)
    _, _, frames, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events",
                                        until=lambda fr: len(_events(fr)) >= 3)
    ids = [int(e["id"]) for e in _events(frames)]
    _, _, resumed, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events", query=f"after_seq={ids[0]}",
                                         until=lambda fr: len(_events(fr)) >= 2)
    assert [int(e["id"]) for e in _events(resumed)] == ids[1:]


async def test_live_events_arrive_after_connect(fastapi_app, inc):
    async def later():
        await asyncio.sleep(0.4)
        await _emit_n(inc.id, 2, stage="live")

    task = asyncio.create_task(later())
    _, _, frames, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events",
                                        until=lambda fr: len(_events(fr)) >= 2, timeout=6)
    await task
    assert [e["json"]["stage"] for e in _events(frames)] == ["live_0", "live_1"]


async def test_streams_are_isolated_per_incident(fastapi_app, session, org, inc):
    other = await f.make_incident(session, org)
    await _emit_n(other.id, 2, stage="other")
    await _emit_n(inc.id, 1, stage="mine")
    _, _, frames, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events",
                                        until=lambda fr: len(_events(fr)) >= 1)
    assert [e["json"]["stage"] for e in _events(frames)] == ["mine_0"]


async def test_no_events_means_no_fabricated_events(fastapi_app, inc):
    _, _, frames, _ = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events", timeout=1.0)
    assert _events(frames) == []


async def test_stream_unknown_incident_404(fastapi_app):
    import uuid

    status, _, _, _ = await asgi_stream(fastapi_app, f"/api/incidents/{uuid.uuid4()}/events", timeout=1.0)
    assert status == 404


async def test_events_history_endpoint_matches_stream(app_client, inc):
    await _emit_n(inc.id, 3)
    r = await app_client.get(f"/api/incidents/{inc.id}/events/history")
    assert [e["stage"] for e in r.json()] == ["crawl_0", "crawl_1", "crawl_2"]
    seq1 = r.json()[0]["seq"]
    r2 = await app_client.get(f"/api/incidents/{inc.id}/events/history", params={"after_seq": seq1})
    assert len(r2.json()) == 2


# ---- bus-level behavior --------------------------------------------------------------------------------


async def test_bus_heartbeat_tick_when_idle(inc, engine):
    bus = EventBus(redis_url="redis://127.0.0.1:1/15")
    it = bus.subscribe(inc.id, heartbeat_seconds=0.1)
    first = await asyncio.wait_for(it.__anext__(), 2)
    assert first is None, "idle stream must emit heartbeat ticks (None), not fabricated events"
    await it.aclose()


async def test_bus_emit_persists_even_without_redis(inc, engine):
    bus = EventBus(redis_url="redis://127.0.0.1:1/15")
    out = await bus.emit(None, inc.id, "stage", StepStatus.RUNNING, "msg", {"k": 1})
    assert out["seq"] >= 1
    assert (await bus.replay(inc.id))[0]["message"] == "msg"
    assert await bus.redis_available() is False


async def test_bus_no_duplicate_delivery_across_replay_and_live(inc, engine):
    bus = EventBus(redis_url="redis://127.0.0.1:1/15")
    await bus.emit(None, inc.id, "a", StepStatus.SUCCESS, "1")
    got = []

    async def consume():
        async for ev in bus.subscribe(inc.id, heartbeat_seconds=0.2):
            if ev is not None:
                got.append(ev["stage"])
            if len(got) == 3:
                return

    t = asyncio.create_task(consume())
    await asyncio.sleep(0.2)
    await bus.emit(None, inc.id, "b", StepStatus.SUCCESS, "2")
    await bus.emit(None, inc.id, "c", StepStatus.SUCCESS, "3")
    await asyncio.wait_for(t, 3)
    assert got == ["a", "b", "c"]


@pytest.mark.skipif(not os.environ.get("AEO_SLOW"), reason="waits for the real 15s SSE ping; set AEO_SLOW=1")
async def test_http_stream_emits_ping_heartbeat(fastapi_app, inc):
    _, _, frames, raw = await asgi_stream(fastapi_app, f"/api/incidents/{inc.id}/events", timeout=20,
                                          until=lambda fr: any("comment" in x for x in fr))
    assert any("comment" in x for x in frames), raw
