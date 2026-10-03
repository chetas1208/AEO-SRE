"""Feature cache: in-process LRU plus an optional Neo4j property copy on the ChangeSet node.

Every entry carries computed_at / feature_version / context_hash. A cached vector is only ever reused for the SAME
(organization, changeset/context key, as_of, feature_version): a different as_of is a different context, so history
is never silently recomputed or reused under another snapshot. No Postgres table is required (see requests-n3.md).
"""
from __future__ import annotations

import json
from collections import OrderedDict
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from app.graph.explain import to_dt
from app.graph.features import as_utc
from app.graph.results import GraphFeatures

if TYPE_CHECKING:
    from app.graph.client import GraphClient

log = structlog.get_logger()
MAX_ENTRIES = 512


class FeatureCache:
    def __init__(self, max_entries: int = MAX_ENTRIES):
        self._d: OrderedDict[tuple, GraphFeatures] = OrderedDict()
        self.max_entries = max_entries

    @staticmethod
    def key(organization_id: str, context_key: str, as_of: datetime, version: str) -> tuple:
        return (organization_id, context_key, as_utc(as_of).isoformat(), version)

    def get(self, key: tuple) -> GraphFeatures | None:
        v = self._d.get(key)
        if v is not None:
            self._d.move_to_end(key)
        return v.model_copy(deep=True) if v is not None else None

    def put(self, key: tuple, value: GraphFeatures) -> None:
        if value.stale or value.unavailable:
            return  # never cache degraded results
        self._d[key] = value.model_copy(deep=True)
        self._d.move_to_end(key)
        while len(self._d) > self.max_entries:
            self._d.popitem(last=False)

    def clear(self) -> None:
        self._d.clear()

    def __len__(self) -> int:
        return len(self._d)


_cache = FeatureCache()


def get_feature_cache() -> FeatureCache:
    return _cache


_PERSIST = (
    "MATCH (c:ChangeSet {changeset_id: $changeset_id, organization_id: $organization_id}) "
    "SET c.gf_version = $version, c.gf_computed_at = $computed_at, c.gf_snapshot_time = $snapshot_time, "
    "c.gf_context_hash = $context_hash, c.gf_payload = $payload RETURN c.changeset_id AS id"
)
_LOAD = (
    "MATCH (c:ChangeSet {changeset_id: $changeset_id, organization_id: $organization_id}) "
    "RETURN c.gf_version AS version, c.gf_computed_at AS computed_at, c.gf_snapshot_time AS snapshot_time, "
    "c.gf_context_hash AS context_hash, c.gf_payload AS payload"
)


async def persist_features(client: GraphClient, features: GraphFeatures) -> bool:
    """Best-effort copy of the computed features onto the ChangeSet node. Returns False (never raises) on failure
    or when the ChangeSet is not in the graph."""
    if features.stale or features.unavailable or not features.changeset_id or not features.organization_id:
        return False
    payload = json.dumps({"vector": features.vector, "names": features.names, "features": features.features,
                          "missing": features.missing}, sort_keys=True)
    try:
        rows = await client.scoped_write(features.organization_id, _PERSIST, {
            "changeset_id": features.changeset_id, "version": features.version,
            "computed_at": (features.computed_at or features.snapshot_time).isoformat() if (features.computed_at or features.snapshot_time) else None,
            "snapshot_time": features.snapshot_time.isoformat() if features.snapshot_time else None,
            "context_hash": features.context_hash, "payload": payload})
        return bool(rows)
    except Exception as exc:  # noqa: BLE001
        log.warning("graph.feature_persist_failed", error=type(exc).__name__)
        return False


async def load_persisted(client: GraphClient, organization_id: str, changeset_id: str, *,
                         as_of: datetime | None = None, version: str | None = None) -> GraphFeatures | None:
    """Load the persisted copy; if `as_of` is given it must equal the stored snapshot_time (exact reproduction)."""
    try:
        rows = await client.scoped_read(organization_id, _LOAD, {"changeset_id": changeset_id})
    except Exception:  # noqa: BLE001
        return None
    if not rows or not rows[0].get("payload"):
        return None
    r: dict[str, Any] = rows[0]
    snap = to_dt(r.get("snapshot_time"))
    if version and r.get("version") != version:
        return None
    if as_of is not None and (snap is None or as_utc(as_of) != snap):
        return None
    p = json.loads(r["payload"])
    return GraphFeatures(
        vector=p["vector"], names=p["names"], version=r["version"], snapshot_time=snap,
        computed_at=to_dt(r.get("computed_at")), context_hash=r.get("context_hash"), stale=False, unavailable=False,
        source="cache", features=p["features"], missing=p["missing"], organization_id=organization_id,
        changeset_id=changeset_id)
