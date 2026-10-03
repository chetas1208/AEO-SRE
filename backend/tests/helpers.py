"""Test helpers: fake clock, emit recorder, ASGI SSE reader, respx utilities."""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class FakeClock:
    """Deterministic clock. Pass `clock.now` wherever a `now=` callable/param is accepted."""

    def __init__(self, start: datetime | None = None):
        self._now = start or datetime(2026, 10, 2, 12, 0, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    __call__ = now

    def advance(self, **kw: float) -> datetime:
        self._now = self._now + timedelta(**kw)
        return self._now


@dataclass
class RecordedEvent:
    incident_id: Any
    stage: str
    status: Any
    message: str
    metadata: dict


@dataclass
class EmitRecorder:
    """Drop-in `emit` callable. Accepts both shapes in use:
    collector style  emit(stage, status, message, metadata)
    bus style        emit(incident_id, stage, status, message, metadata)  (optionally with a leading session)."""

    events: list[RecordedEvent] = field(default_factory=list)

    def _record(self, *args, **kw):
        from app.domain.enums import StepStatus
        from sqlalchemy.ext.asyncio import AsyncSession

        args = list(args)
        if args and (args[0] is None or isinstance(args[0], AsyncSession)):
            args.pop(0)
        valid = {s.value for s in StepStatus}

        def is_status(x):
            return str(getattr(x, "value", x)) in valid

        incident_id = kw.get("incident_id")
        if len(args) >= 3 and is_status(args[2]) and not is_status(args[1]):
            incident_id, stage, status, *rest = args
        else:
            stage, status, *rest = args
        message = rest[0] if rest else kw.get("message", "")
        metadata = (rest[1] if len(rest) > 1 else kw.get("metadata")) or {}
        self.events.append(RecordedEvent(incident_id, stage, getattr(status, "value", status), message, dict(metadata)))

    async def __call__(self, *args, **kw):
        self._record(*args, **kw)

    def sync(self, *args, **kw):
        self._record(*args, **kw)

    @property
    def stages(self) -> list[str]:
        return [e.stage for e in self.events]

    def statuses(self) -> set[str]:
        return {e.status for e in self.events}


def mutating_calls(router) -> list:
    """All recorded respx calls with a mutating HTTP method."""
    return [c for c in router.calls if c.request.method.upper() in MUTATING_METHODS]


def parse_sse(raw: bytes | str) -> list[dict]:
    """Parse an SSE byte stream into frames: {id,event,data,comment,retry}."""
    text = raw.decode() if isinstance(raw, bytes) else raw
    frames: list[dict] = []
    for block in text.replace("\r\n", "\n").split("\n\n"):
        if not block.strip():
            continue
        frame: dict[str, Any] = {}
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith(":"):
                frame.setdefault("comment", []).append(line[1:].strip())
            elif ":" in line:
                k, v = line.split(":", 1)
                v = v.removeprefix(" ")
                if k == "data":
                    data_lines.append(v)
                else:
                    frame[k] = v
        if data_lines:
            frame["data"] = "\n".join(data_lines)
            try:
                frame["json"] = json.loads(frame["data"])
            except ValueError:
                pass
        frames.append(frame)
    return frames


async def asgi_stream(
    app,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    until: Callable[[list[dict]], bool] | None = None,
    timeout: float = 5.0,
    query: str = "",
) -> tuple[int | None, dict[str, str], list[dict], bytes]:
    """Run an ASGI GET against `app`, collect the streamed body until `until(frames)` or timeout, then disconnect.

    httpx's ASGITransport buffers whole responses so it cannot test infinite SSE streams; this can.
    """
    hdrs = [(b"host", b"test"), (b"accept", b"text/event-stream")]
    for k, v in (headers or {}).items():
        hdrs.append((k.lower().encode(), v.encode()))
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": query.encode(), "headers": hdrs,
        "client": ("127.0.0.1", 1), "server": ("test", 80), "root_path": "",
    }
    disconnect = asyncio.Event()
    sent_body = False
    status: int | None = None
    resp_headers: dict[str, str] = {}
    buf = bytearray()
    done = asyncio.Event()

    async def receive():
        nonlocal sent_body
        if not sent_body:
            sent_body = True
            return {"type": "http.request", "body": b"", "more_body": False}
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        nonlocal status
        if message["type"] == "http.response.start":
            status = message["status"]
            resp_headers.update({k.decode().lower(): v.decode() for k, v in message.get("headers", [])})
        elif message["type"] == "http.response.body":
            buf.extend(message.get("body", b""))
            if not message.get("more_body", False):
                done.set()

    async def run():
        try:
            await app(scope, receive, send)
        finally:
            done.set()

    task = asyncio.create_task(run())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    try:
        while loop.time() < deadline:
            if until is not None and until(parse_sse(bytes(buf))):
                break
            if done.is_set():
                break
            await asyncio.sleep(0.02)
    finally:
        disconnect.set()
        await asyncio.sleep(0.05)
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass
    return status, resp_headers, parse_sse(bytes(buf)), bytes(buf)
