# Third-party references (A10: Executor / GitHub)

| Repo | SHA | License | Code reused | Concepts adapted | Attribution |
|---|---|---|---|---|---|
| https://github.com/mogonlac/hackupc2026 (SLAP) -> `references/slap` | 89eb5b3f66d9fda07ed5ef16c750e220b05ec5d7 | No LICENSE file; README says "HackUPC 2026 License" (undefined); backend/package.json says ISC without text. Treated as ambiguous. | **No** | LLM structured JSON edit -> PR handoff; human-gated change; branch/commit/PR flow (all reimplemented; no merge, deterministic branch names, grounding gate) | Credit in THIRD_PARTY.md as inspiration only |
| https://github.com/A-Piyas-04/Recoil-AI -> `references/recoil-ai` | a65f14fa2a3b6e8a9d687f31d6008678772c16b3 | None (README placeholder "Add your license here") | **No** | Grounded-generation instruction; risk decomposition / pre-approval challenge | Inspiration only |

Runtime dependencies added by A10: none (httpx, pydantic, structlog, respx/pytest already present).
