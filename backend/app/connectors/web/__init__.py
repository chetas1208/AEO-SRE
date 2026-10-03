"""Public-web evidence connector (A4): WebCollector.fetch/discover_urls, FetchResult, diff_snapshots."""

from app.connectors.web.cache import ResultCache
from app.connectors.web.collector import FetchError, WebCollector, host_matches, registrable_host
from app.connectors.web.diff import SnapshotDiff, diff_snapshots
from app.connectors.web.ssrf import UnsafeURL, is_public_ip, validate_url
from app.connectors.web.types import Block, FetchResult, SnapshotStore, hash_text

__all__ = [
    "Block",
    "FetchError",
    "FetchResult",
    "ResultCache",
    "SnapshotDiff",
    "SnapshotStore",
    "UnsafeURL",
    "WebCollector",
    "diff_snapshots",
    "hash_text",
    "host_matches",
    "is_public_ip",
    "registrable_host",
    "validate_url",
]
