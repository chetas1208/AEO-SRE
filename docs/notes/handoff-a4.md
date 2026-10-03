# Handoff A4 (Info-Ninja / public-web collector)

## What exists
- `backend/app/connectors/web/` (`types.py`, `ssrf.py`, `robots.py`, `extract.py`, `cache.py`, `collector.py`, `diff.py`)
- `backend/app/investigation/collector.py`
- `backend/tests/unit/test_web_collector.py` (70 tests, respx, no real network)
- `docs/notes/info-ninja.md`, `docs/notes/third-party-a4.md`; reference clone at `references/info-ninja/` (no LICENSE -> ideas only)

## Public interfaces
```python
from app.connectors.web import WebCollector, FetchResult, Block, ResultCache, diff_snapshots, validate_url
async with WebCollector(user_agent=..., timeout=15, max_bytes=2_000_000, max_redirects=5,
                        per_host_concurrency=2, per_host_min_interval=1.0, global_concurrency=8,
                        respect_robots=True, cache=ResultCache(ttl_seconds=900)) as c:
    r = await c.fetch(url, use_cache=True)      # never raises
    urls = await c.discover_urls("acme.com", limit=50, keywords=["pricing"])
```
`FetchResult(url, final_url, status, http_status, content_hash, text, blocks, title, fetched_at, error,
last_modified, content_type, retrieval_method, from_cache)`. `status` is an `EvidenceStatus` value:
`live` | `unavailable` (robots/SSRF block, 401/403/404/410, non-text, empty/JS shell) | `failed` (timeout,
connect/DNS error, 5xx/429, oversize, too many redirects). Failed results have empty text/blocks and
`content_hash=None`.
`diff_snapshots(prev, new) -> SnapshotDiff(kind new|unchanged|changed|unavailable, changed, added, removed, modified, ...)`;
`prev`/`new` may be `FetchResult`, dict, or an `Evidence` row (blocks read from `raw["block_index"]`).
`SnapshotStore` Protocol (`latest(url)`, `save(result)`) is the persistence hook; default impl
`EvidenceRowSnapshotStore(session)` reads the newest `Evidence` row per URL (storage of record = Evidence).

```python
from app.investigation.collector import collect_evidence
rows = await collect_evidence(session, incident, emit, collector=None, ranker=None, max_pages=24)
```
- **`emit` signature is `emit(stage, status, message, metadata)`** (sync or async; exceptions swallowed). The
  EventBus method is `emit(session, incident_id, stage, status, message, metadata)`, and A13's `EmitRecorder`
  takes `incident_id` first, so the pipeline (A14) should pass
  `functools.partial`/lambda: `lambda s, st, m, md: bus.emit(None, incident.id, s, st, m, md)`.
- Adds + flushes Evidence rows (does not commit). Returned sorted by relevance desc.
- Stages emitted: `web.discover.started|completed`, `web.discover.domain` (warning), `web.fetch.started`,
  `web.fetch.page` (one per URL), `web.fetch.completed` (counts per status), `web.relevance.completed`.
- Evidence mapping: `type` owned (own + canonical domains/subdomains) / competitor / external; `status`
  live | changed (hash differs from prior Evidence row for that URL) | stale (page-declared modified date >365 d)
  | unavailable | failed; `content_hash` sha256 of normalized text (None when not live); `excerpt` best
  relevant block (empty if none/failed); `retrieval_method` `httpx+trafilatura|httpx+bs4|httpx+text|cache`;
  `confidence` = relevance (0-1 keyword/entity overlap) for live pages, 0.0 otherwise; `raw` carries
  final_url, http_status, error, relevant_blocks, block_index (heading+hash, for later diffs), diff, ranker output.
- Inputs resolved from `incident` + DB: org (domain, competitor_domains, canonical_domains, topics),
  PromptCluster (topic, prompts), `incident.context` keys `cited_urls|citations|urls`, `topic`, `prompts`,
  `keywords`, plus Signals whose `kind` contains "citation" (URLs mined from `raw`).
- Ranker: optional `ranker` arg (callable `(claim, passage, meta)` or object with `.score`); if omitted tries
  `app.evidence.ranker.EvidenceRanker.load()`; absent -> support/contradiction/insufficient/freshness left `None`
  (never invented).

## Safety behavior
http/https only; ports 80/443/8080/8443; no URL credentials; localhost/.internal/.local blocked; DNS resolved and
all addresses must be globally routable; redirects followed manually (max 5) with SSRF + robots re-checked at every
hop; robots.txt: 4xx = allow, 5xx/unreachable = disallow (status `unavailable`), Crawl-delay honored (cap 10 s);
per-host concurrency 2 + 1 s spacing, global concurrency 8; 15 s timeout; 2 MB body cap (robots 500 KB, sitemaps 5 MB);
sitemap URLs are regex-extracted (no XML entity expansion), same-domain only, gzip supported.

## Verified
- `AEO_TEST_DB=sqlite .venv/bin/python -m pytest tests/unit/test_web_collector.py` -> 70 passed (SSRF, redirects to
  private IPs, robots, failed fetch, oversize, cache, Redis-down fallback, change diff, discovery, collect_evidence on DB).
- Live smoke (2026-10-02): `https://example.com/` -> `live`, HTTP 200, title "Example Domain", sha256 `27606550...`,
  `httpx+trafilatura`; second call served from Redis (`from_cache=True`, backend `redis`); `discover_urls("example.com")`
  -> `[]` (no sitemap, nothing guessed); `http://169.254.169.254/...` -> `unavailable` (blocked); unresolvable host -> `failed`.

## Known gaps
- DNS rebinding TOCTOU: we validate resolved IPs but httpx re-resolves at connect time (mitigation: egress firewall).
- No JS rendering; SPA pages come back `unavailable` ("no extractable text").
- `stale` only when the page declares a modified date (meta/JSON-LD/Last-Modified); otherwise never guessed.
- Relevance is keyword/entity overlap (deterministic), not semantic; the ranker (A8) supplies semantic scores.
- Redis cache is URL->result for 15 min; long-term snapshots live in Evidence rows only.
- Running the shared test suite against Postgres concurrently with other agents deadlocks on `DROP SCHEMA`
  in conftest; use `AEO_TEST_DB=sqlite` for isolated runs.

## Requests for others
- A14: wire `collect_evidence` into the pipeline with the emit adapter above; commit.
- A8: `EvidenceRanker.load()` classmethod and `.score(claim, passage, meta)` as in brief; meta keys passed:
  url, title, type, owned, source_age_days, status.
- A12/A6: no changes needed; Evidence `type`/`status` are stored as string values.
