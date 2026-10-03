"""Raw payload persistence interface. Payloads are stored unchanged (never credentials/headers)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

REF_PREFIX = "profound://"


def payload_ref(endpoint: str, request: Mapping[str, Any] | None, payload: Any) -> str:
    """Content-addressed reference: identical (endpoint, request, payload) -> identical ref."""
    blob = json.dumps({"e": endpoint, "q": request or {}, "p": payload}, sort_keys=True, default=str)
    return REF_PREFIX + hashlib.sha256(blob.encode()).hexdigest()[:32]


@runtime_checkable
class RawPayloadStore(Protocol):
    async def save(
        self, *, endpoint: str, request: Mapping[str, Any] | None, payload: Any, fetched_at: datetime | None = None
    ) -> str:
        """Persist the unchanged response body and return a stable reference (goes in Signal.raw_payload_ref)."""
        ...

    async def load(self, ref: str) -> dict[str, Any] | None: ...


class InMemoryRawPayloadStore:
    def __init__(self) -> None:
        self.items: dict[str, dict[str, Any]] = {}

    async def save(self, *, endpoint, request, payload, fetched_at=None) -> str:
        ref = payload_ref(endpoint, request, payload)
        self.items.setdefault(
            ref,
            {"endpoint": endpoint, "request": dict(request or {}), "payload": payload,
             "fetched_at": (fetched_at or datetime.now(UTC)).isoformat()},
        )
        return ref

    async def load(self, ref: str):
        return self.items.get(ref)


class FileRawPayloadStore:
    """One JSON file per payload under `root` (default data/profound_raw, gitignored via data/*)."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)

    def _path(self, ref: str) -> Path:
        return self.root / (ref.removeprefix(REF_PREFIX) + ".json")

    async def save(self, *, endpoint, request, payload, fetched_at=None) -> str:
        ref = payload_ref(endpoint, request, payload)
        path = self._path(ref)
        if not path.exists():
            self.root.mkdir(parents=True, exist_ok=True)
            doc = {"endpoint": endpoint, "request": dict(request or {}), "payload": payload,
                   "fetched_at": (fetched_at or datetime.now(UTC)).isoformat()}
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps(doc, default=str))
            tmp.replace(path)
        return ref

    async def load(self, ref: str):
        path = self._path(ref)
        return json.loads(path.read_text()) if path.exists() else None
