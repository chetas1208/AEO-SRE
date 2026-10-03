"""Public-web connector types. `status` reuses EvidenceStatus values: live | unavailable | failed."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from app.domain.enums import EvidenceStatus


@dataclass(slots=True)
class Block:
    """Heading-scoped extracted text block."""

    heading: str
    level: int
    text: str
    index: int = 0
    hash: str = ""

    def __post_init__(self) -> None:
        if not self.hash:
            self.hash = hash_text(f"{self.heading}\n{self.text}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class FetchResult:
    url: str
    final_url: str
    status: str  # EvidenceStatus value: live | unavailable | failed
    http_status: int | None = None
    content_hash: str | None = None
    text: str = ""
    blocks: list[Block] = field(default_factory=list)
    title: str = ""
    fetched_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error: str | None = None
    last_modified: datetime | None = None
    content_type: str | None = None
    retrieval_method: str = "httpx"
    from_cache: bool = False
    normalized_hash: str | None = None  # hash of non-boilerplate blocks; boilerplate-only edits do not move it

    @property
    def ok(self) -> bool:
        return self.status == EvidenceStatus.LIVE.value

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "final_url": self.final_url,
            "status": self.status,
            "http_status": self.http_status,
            "content_hash": self.content_hash,
            "text": self.text,
            "blocks": [b.to_dict() for b in self.blocks],
            "title": self.title,
            "fetched_at": self.fetched_at.isoformat(),
            "error": self.error,
            "last_modified": self.last_modified.isoformat() if self.last_modified else None,
            "content_type": self.content_type,
            "retrieval_method": self.retrieval_method,
            "from_cache": self.from_cache,
            "normalized_hash": self.normalized_hash,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FetchResult:
        def _dt(v: Any) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            url=d["url"],
            final_url=d.get("final_url") or d["url"],
            status=d["status"],
            http_status=d.get("http_status"),
            content_hash=d.get("content_hash"),
            text=d.get("text", ""),
            blocks=[Block(**b) for b in d.get("blocks", [])],
            title=d.get("title", ""),
            fetched_at=_dt(d.get("fetched_at")) or datetime.now(UTC),
            error=d.get("error"),
            last_modified=_dt(d.get("last_modified")),
            content_type=d.get("content_type"),
            retrieval_method=d.get("retrieval_method", "httpx"),
            from_cache=bool(d.get("from_cache", False)),
            normalized_hash=d.get("normalized_hash"),
        )


@runtime_checkable
class SnapshotStore(Protocol):
    """Persistence hook for page snapshots. Storage of record is Evidence rows (A6); duck-typed.

    `latest` may return a FetchResult, a dict with content_hash/blocks, or an Evidence-like row
    exposing `content_hash` and `raw`.
    """

    async def latest(self, url: str) -> Any | None: ...

    async def save(self, result: FetchResult) -> None: ...


def hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
