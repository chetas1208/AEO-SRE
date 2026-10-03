"""Provenance primitives: content hashing, the Provenance record, and immutable evidence snapshots."""
import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any
from urllib.parse import urlparse
from uuid import UUID

PROVENANCE_FIELDS = ("source", "timestamp", "confidence", "extract", "hash", "retrieval_method")
SNAPSHOT_VERSION = 1

_WS = re.compile(r"\s+")

# Evidence attributes frozen into an experiment's evidence_snapshot (excerpt included: it is the quoted source text).
_SNAPSHOT_ATTRS = (
    "type", "status", "title", "source", "url", "content_hash", "excerpt", "retrieval_method",
    "retrieved_at", "observed_at", "support_score", "contradiction_score", "insufficient_score",
    "freshness_risk", "confidence",
)


def normalize_text(text: str | None) -> str:
    """NFKC, unified newlines, collapsed whitespace, stripped. Case is preserved: a case change is a change."""
    if not text:
        return ""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text)).strip()


def content_hash(text: str | bytes | None) -> str | None:
    """sha256 hex of normalized text. None for missing/blank content (never hash 'nothing' as evidence)."""
    if text is None:
        return None
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="replace")
    normalized = normalize_text(text)
    if not normalized:
        return None
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        return [_jsonable(v) for v in value]
    return value


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def canonical_json(value: Any) -> str:
    return json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def hash_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def domain_of(url: str | None) -> str | None:
    if not url:
        return None
    host = urlparse(url if "://" in url else f"//{url}").hostname
    return host.removeprefix("www.") if host else None


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a node/edge came from. Missing fields stay None and are listed in `missing` (never invented)."""

    source: str | None = None
    timestamp: datetime | str | None = None
    confidence: float | None = None
    extract: str | None = None
    hash: str | None = None
    retrieval_method: str | None = None
    missing: tuple[str, ...] = field(default=(), compare=False)

    @classmethod
    def make(cls, **values: Any) -> "Provenance":
        """Construct from the six fields; any absent/blank field is recorded in `missing`."""
        vals = {k: values.get(k) for k in PROVENANCE_FIELDS}
        return cls(**vals, missing=tuple(k for k in PROVENANCE_FIELDS if vals[k] in (None, "")))

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "Provenance":
        return cls.make(**(data or {}))

    @classmethod
    def from_evidence(cls, ev: Any) -> "Provenance":
        return cls.make(
            source=_get(ev, "url") or _get(ev, "source"),
            timestamp=_get(ev, "retrieved_at") or _get(ev, "observed_at"),
            confidence=_get(ev, "confidence"),
            extract=_get(ev, "excerpt"),
            hash=_get(ev, "content_hash"),
            retrieval_method=_get(ev, "retrieval_method"),
        )

    @property
    def complete(self) -> bool:
        return not self.missing

    def to_dict(self) -> dict[str, Any]:
        d = {k: _jsonable(getattr(self, k)) for k in PROVENANCE_FIELDS}
        d["missing"] = list(self.missing)
        return d


def edge_provenance(
    *, source: str, extract: str | None, confidence: float | None, retrieval_method: str,
    timestamp: datetime | None = None, content: str | None = None, hash: str | None = None,
) -> dict[str, Any]:
    """Build the JSON stored in EvidenceEdge.provenance. Hash is of `content`, else of the extract."""
    return {
        "source": source,
        "timestamp": _jsonable(timestamp),
        "confidence": confidence,
        "extract": extract,
        "hash": hash or content_hash(content) or content_hash(extract),
        "retrieval_method": retrieval_method,
    }


def extract_for(ev: Any) -> tuple[str | None, str]:
    """(text, kind). kind is 'excerpt' (quoted source text), 'title_fallback' (a page title: it names a page, it is NOT
    article evidence) or 'none'. Edge provenance records the kind so a title can never pass as an extract."""
    excerpt = normalize_text(_get(ev, "excerpt"))
    if excerpt:
        return excerpt, "excerpt"
    title = normalize_text(_get(ev, "title"))
    if title:
        return title, "title_fallback"
    return None, "none"


def evidence_snapshot(evidence_list: Iterable[Any]) -> dict[str, Any]:
    """Immutable, JSON-safe, deterministic snapshot of the evidence an experiment decision relied on.

    Items are sorted by id and detached from the ORM objects (deep-copied primitives). `snapshot_hash`
    covers the items so later mutation of the evidence rows (or of the stored snapshot) is detectable
    via `verify_snapshot`.
    """
    items = []
    for ev in evidence_list:
        item = {"id": str(_get(ev, "id"))}
        item.update({attr: _jsonable(_get(ev, attr)) for attr in _SNAPSHOT_ATTRS})
        items.append(item)
    items.sort(key=lambda i: i["id"])
    snapshot = {"version": SNAPSHOT_VERSION, "count": len(items), "items": items}
    snapshot["snapshot_hash"] = hash_json({"version": SNAPSHOT_VERSION, "items": items})
    return snapshot


def verify_snapshot(snapshot: Mapping[str, Any]) -> bool:
    items = snapshot.get("items")
    expected = snapshot.get("snapshot_hash")
    if items is None or expected is None:
        return False
    return hash_json({"version": snapshot.get("version", SNAPSHOT_VERSION), "items": items}) == expected


def snapshot_drift(snapshot: Mapping[str, Any], current_evidence: Iterable[Any]) -> list[dict[str, Any]]:
    """Compare a stored snapshot to live evidence rows; report changed/missing items by content_hash and status."""
    live = {str(_get(ev, "id")): ev for ev in current_evidence}
    drift = []
    for item in snapshot.get("items", []):
        ev = live.get(item["id"])
        if ev is None:
            drift.append({"id": item["id"], "change": "missing"})
            continue
        for attr in ("content_hash", "status"):
            now = _jsonable(_get(ev, attr))
            if now != item.get(attr):
                drift.append({"id": item["id"], "change": attr, "snapshot": item.get(attr), "current": now})
    return drift
