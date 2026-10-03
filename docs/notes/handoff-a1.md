# Handoff A1 (Auditor)

Deliverable: `docs/notes/audit.md` (only file besides this one; no code written, no git commands run).

Key findings
- Env healthy: uv 0.12.17, venv Python 3.12.3 with all backend + ML deps installed (CPU only; CUDA unusable with this torch build), node 22 / pnpm 11, Postgres (pgvector image, DBs `aeo` + `aeo_test`) and Redis up. Docker is rootless and needs `DOCKER_HOST=unix:///tmp/xdg-1989081997/docker.sock`. pgvector extension is not created. `gh` authed as chetas1208 (repo scope). Workspace path contains spaces.
- `.env`: `PROFOUND_API_KEY`, `PROFOUND_BASE_URL`, `MODEL_API_KEY`, `GITHUB_TOKEN/OWNER/REPO` are all unset. Executor stays dry-run; LLM and Profound must degrade to `unavailable`.
- Profound API is public-documented (OpenAPI at https://api.tryprofound.com/openapi.json): base `https://api.tryprofound.com`, header `X-API-Key`, Enterprise/on-request, 600 req/h, ET dates (v2 end inclusive). Endpoints exist for visibility, citations, FactCheck (scores + claims), raw answers, tracked prompts, agents (list/get/run/poll). Prompt volume is keyword-level on-the-fly (1,000 keywords/day), not per tracked prompt. Never tested with a real key.
- Datasets: `tals/vitaminc` (488,904 rows, 36 MB parquet, CC-BY-SA-3.0) and `copenlu/fever_gold_evidence` (260,251 rows, 56 MB, CC-BY-SA-3.0/GPL-3.0) load without auth or scripts. `fever/fever` is script-based, avoid.
- 21 doc tensions with resolutions are in audit.md section 3. Most important: enum names beat Plan names (actions, experiment status); UI.md Implementation Spec (3 tabs) is normative; add a display_state mapping for the 17 IncidentState values; add global SSE and expected_outcome (n-gated) to the API; Plan "awaiting_reward" == `awaiting_verification`.

Requests for others (no files edited)
- A14: add `*.egg-info/` to `.gitignore`; default `profound_base_url` to `https://api.tryprofound.com`; decide pgvector; decide GitHub target repo/token source.
- A12: `display_state`, `expected_outcome`, global `/api/events`, `range` param, number-or-UUID ids.
- A7: use the OpenAPI spec for schemas; capabilities unavailable when no key.
- A8: VitaminC primary, fever_gold_evidence secondary; verify NEI evidence coverage; MiniLM is cached locally.
