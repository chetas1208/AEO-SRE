import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import BusDep
from app.graph.sse import graph_projected_events, merge_streams

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_class=EventSourceResponse, responses={200: {"description": "Server-sent events", "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def global_events(request: Request, bus: BusDep):
    """Global SSE feed: named `heartbeat` events (every 10s; the UI Live pill binds to these) and
    `incident_event` events (every persisted incident event, e.g. detected/state changes) so lists
    refresh without polling, and `graph_projected` events (coalesced, tiny: organization_id, projected, last_event_id,
    last_event_type, backlog) so the topology can refetch after the graph projector commits. No replay; clients
    refetch lists on (re)connect."""

    async def stream():
        async for item in merge_streams(bus.subscribe_global(heartbeat_seconds=10), graph_projected_events()):
            kind = item["type"]
            yield {"event": kind, "data": json.dumps(item)}

    return EventSourceResponse(stream(), ping=5, headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"})
