"""Change detection between two page snapshots (duck-typed: FetchResult, dict, or Evidence-like row)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.connectors.web.extract import is_boilerplate_text
from app.connectors.web.types import hash_text


@dataclass(slots=True)
class SnapshotDiff:
    kind: str  # new | unchanged | changed | unavailable
    changed: bool
    prev_hash: str | None = None
    new_hash: str | None = None
    added: list[dict[str, Any]] = field(default_factory=list)
    removed: list[dict[str, Any]] = field(default_factory=list)
    modified: list[dict[str, Any]] = field(default_factory=list)
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "changed": self.changed,
            "prev_hash": self.prev_hash,
            "new_hash": self.new_hash,
            "added": self.added,
            "removed": self.removed,
            "modified": self.modified,
            "summary": self.summary,
        }


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def snapshot_hash(snap: Any) -> str | None:
    return _get(snap, "content_hash")


def snapshot_status(snap: Any) -> str | None:
    return _get(snap, "status")


def snapshot_normalized_hash(snap: Any) -> str | None:
    h = _get(snap, "normalized_hash")
    if h:
        return h
    raw = _get(snap, "raw")
    return raw.get("normalized_hash") if isinstance(raw, dict) else None


def snapshot_blocks(snap: Any) -> list[dict[str, Any]]:
    """Normalize blocks to [{heading, hash, text?}]. Evidence rows keep them under raw['block_index']."""
    blocks = _get(snap, "blocks")
    if blocks is None:
        raw = _get(snap, "raw") or {}
        blocks = raw.get("block_index") or raw.get("blocks") or []
    out = []
    for b in blocks:
        heading = _get(b, "heading", "") or ""
        text = _get(b, "text")
        h = _get(b, "hash") or (hash_text(f"{heading}\n{text}") if text is not None else None)
        if h and not (text is not None and is_boilerplate_text(text)):  # cookie/footer/legal lines never count
            out.append({"heading": heading, "hash": h, "text": text})
    return out


def diff_snapshots(prev: Any | None, new: Any) -> SnapshotDiff:
    """Compare two snapshots by content hash and heading-scoped blocks.

    A failed/unavailable `new` snapshot is never reported as a content change (no claim is made).
    """
    new_hash = snapshot_hash(new)
    if snapshot_status(new) in ("failed", "unavailable") or not new_hash:
        return SnapshotDiff(
            "unavailable",
            False,
            snapshot_hash(prev),
            None,
            summary="current snapshot unavailable; no change claim made",
        )
    prev_hash = snapshot_hash(prev)
    if prev is None or not prev_hash or snapshot_status(prev) in ("failed", "unavailable"):
        return SnapshotDiff("new", False, None, new_hash, summary="no prior live snapshot to compare")
    if prev_hash == new_hash:
        return SnapshotDiff("unchanged", False, prev_hash, new_hash, summary="content hash identical")
    prev_norm, new_norm = snapshot_normalized_hash(prev), snapshot_normalized_hash(new)
    if prev_norm and new_norm and prev_norm == new_norm:
        return SnapshotDiff(
            "unchanged", False, prev_hash, new_hash,
            summary="only boilerplate (cookie banner, footer, legal, navigation) differs; no content change",
        )
    old_blocks, new_blocks = snapshot_blocks(prev), snapshot_blocks(new)
    old_hashes, new_hashes = {b["hash"] for b in old_blocks}, {b["hash"] for b in new_blocks}
    gone = [b for b in old_blocks if b["hash"] not in new_hashes]
    fresh = [b for b in new_blocks if b["hash"] not in old_hashes]
    modified, added, removed = [], [], []
    gone_by_heading: dict[str, list[dict]] = {}
    for b in gone:
        gone_by_heading.setdefault(b["heading"], []).append(b)
    for b in fresh:
        pool = gone_by_heading.get(b["heading"])
        if b["heading"] and pool:
            old = pool.pop(0)
            modified.append({"heading": b["heading"], "old": old.get("text"), "new": b.get("text")})
        else:
            added.append({"heading": b["heading"], "text": b.get("text")})
    for pool in gone_by_heading.values():
        removed.extend({"heading": b["heading"], "text": b.get("text")} for b in pool)
    if not added and not removed and not modified:
        return SnapshotDiff("unchanged", False, prev_hash, new_hash, summary="no section-level content change")
    summary = (
        f"content hash changed: {len(added)} added, {len(removed)} removed, {len(modified)} modified blocks"
    )
    return SnapshotDiff("changed", True, prev_hash, new_hash, added, removed, modified, summary)
