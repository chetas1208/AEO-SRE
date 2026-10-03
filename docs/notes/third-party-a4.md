# Third-party references (A4)

| Repo | SHA | License | Code reused | Files / concepts adapted | Attribution |
|---|---|---|---|---|---|
| https://github.com/1300Sarthak/mcp-hackathon-sf (Info-Ninja) | `ee2bfea80c811c1a41844e0aae69e2d7d654a413` | None found at this SHA (no LICENSE file) | **No** | Concepts only, reimplemented from scratch: TTL result cache with graceful Redis fallback; SSE-style stage progress events; multi-source research fan-out. No code, prompts, or text copied. | Idea credit in `docs/notes/info-ninja.md`; no attribution required for non-copied ideas, but listed for transparency |

Runtime dependencies used by A4 code (all already in `backend/pyproject.toml`, none added): httpx (BSD-3),
trafilatura (Apache-2.0), beautifulsoup4 (MIT), lxml (BSD-3), redis-py (MIT); stdlib `urllib.robotparser`.
Test-only: respx (BSD-3).
