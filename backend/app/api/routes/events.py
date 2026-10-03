import json

from fastapi import APIRouter, Request
from sse_starlette.sse import EventSourceResponse

from app.api.deps import BusDep

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events", response_class=EventSourceResponse, responses={200: {"description": "Server-sent events", "content": {"text/event-stream": {"schema": {"type": "string"}}}}})
async def global_events(request: Request, bus: BusDep):
    """Global SSE feed: named `heartbeat` events (every 10s; the UI Live pill binds to these) and
    `incident_event` events (every persisted incident event, e.g. detected/state changes) so lists
    refresh without polling. No replay; clients refetch lists on (re)connect."""

    async def stream():
        async for item in bus.subscribe_global(heartbeat_seconds=10):
            kind = item["type"]
            yield {"event": kind, "data": json.dumps(item)}

    return EventSourceResponse(stream(), headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
