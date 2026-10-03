# Info-Ninja reference study (A4)

Repo: https://github.com/1300Sarthak/mcp-hackathon-sf (cloned to `references/info-ninja/`, depth 50)
SHA: `ee2bfea80c811c1a41844e0aae69e2d7d654a413`
License: **none found** (no LICENSE/COPYING file at the SHA; no license field in README or package files).
Gate result: all rights reserved by default -> **no code copied**. Ideas only, reimplemented independently.

## What it is
A competitive-intelligence demo (FastAPI + React). LLM agents (Strands + Gemini) drive Bright Data MCP
scraping, a researcher -> analyst -> writer chain produces a report; Redis caches finished analyses.
Not a deterministic crawler; fetching is delegated to a paid scraping MCP, so there is no robots.txt,
SSRF, hashing, or diff logic to learn from.

## Patterns observed (and what we did instead)
| Pattern in repo | Where | Our independent take |
|---|---|---|
| Multi-source research pipeline = LLM agent chain with tool calls over 10+ source types | `api/ci_agent.py` | Deterministic collector: owned / canonical / competitor / Profound-cited URLs, each classified by domain; LLM is not in the fetch path |
| Redis cache of whole-analysis JSON, key = `ci:<prefix>:md5(data)`, TTL 1h/24h, silently disabled when Redis is down | `api/redis_cache.py` | `ResultCache` keyed by sha256(url), async redis, TTL 15 min, automatic in-memory fallback with 30 s retry backoff; only `live` results cached so failures are never replayed as truth |
| SSE: queue fed by agent callback, `data: {json}\n\n` frames, heartbeat on idle, terminal error event | `api/app.py` `analyze_competitor_stream` | We only call `emit(stage, status, message, metadata)`; persistence + SSE fan-out is `app.core.events.EventBus` (A12). Stage names `web.discover.*`, `web.fetch.*`, `web.relevance.completed` |
| "Normalized" output = regex-extracted metrics from LLM prose | `ci_agent.extract_metrics_from_analysis` | Normalized evidence = `FetchResult` (url, final_url, status, http_status, sha256 of normalized text, heading-scoped blocks, title, fetched_at, error) then `Evidence` rows with hash + excerpt + retrieval_method |
| Cache refresh / clear endpoints per competitor | `api/app.py` | `fetch(url, use_cache=False)` forces refresh |

## Lessons carried over
- Cache must degrade gracefully (Redis down must not break the run).
- Progress events should be emitted per step, not only at the end.
- Weakness to avoid: caching and presenting LLM-synthesized text as source evidence.
