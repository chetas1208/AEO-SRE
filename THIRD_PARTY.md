# Third-party software and attribution

This file lists (1) the third-party software, datasets and models AEO SRE incorporates, with licenses, and (2) the
research and prior work that informed the design. **Audit result (2026-10-03):** no source code from any studied
open-source or hackathon project is present in this repository; the application code under `backend/app` and
`frontend/` was written for this project. A mechanical comparison of the studied repositories against production source
found only generic boilerplate (import blocks, the standard Alembic `env.py` template, one-line framework idioms such as
`model_config = ConfigDict(from_attributes=True)`). Production code never imports or reads `references/`, which is
gitignored and not part of this repository. There is therefore no copied-code license notice to carry.

## Incorporated third-party software, data and models

Only items that AEO SRE actually uses, trains on or distributes. Licenses are as recorded from the upstream dataset/model cards and package metadata on the dates shown; ShareAlike obligations are noted where they apply.

| Item | Version | License | Use | Notes |
|---|---|---|---|---|
| FEVER (`copenlu/fever_gold_evidence` on Hugging Face) | hub sha `a6b8d891d393e97a4efac791afffb2d7de5e57c6` (card checked 2026-10-02); snapshot also in `backend/ml/artifacts/evidence_ranker/manifest.json` | HF card tags `cc-by-sa-3.0` and `gpl-3.0` (Wikipedia-derived; ShareAlike) | Trains the EvidenceRanker (subset). Raw data is **not** in the repo | ShareAlike: the trained classifier weights under `backend/ml/artifacts/evidence_ranker/` are a derived model; if redistributed outside this project, attribute and treat as CC BY-SA-3.0-encumbered or retrain without it. Wikipedia-style claims; web-page transfer unvalidated |
| VitaminC (`tals/vitaminc` on Hugging Face) | hub sha `be6febb761b0b2807687e61e0b5282e459df2fa0` (card checked 2026-10-02) | CC BY-SA 3.0 (Wikipedia-derived) | Trains the EvidenceRanker (subset) | Same ShareAlike note as FEVER. Freshness head showed no signal (AUC 0.502) and is disabled |
| `sentence-transformers/all-MiniLM-L6-v2` | hub sha `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` (card checked 2026-10-02) | Apache-2.0 | Sentence embeddings for the ranker features | Loaded lazily; ranker degrades to lexical/rules without it |
| Open Bandit Pipeline `obp` (st-tech/zr-obp) | 0.4.1 | Apache-2.0 | Independent reference implementation in a throwaway venv, used only by `backend/app/learning/obp_validation.py` to validate our LinUCB/IPS/SNIPS/DR code | Not a backend dependency; results in `docs/notes/a9-obp-validation.json` |
| Open Bandit Dataset (random-policy "men" sample, 10k rows bundled with obp) | obp 0.4.1 | CC BY 4.0 (ZOZO) | Validation of bandit/OPE machinery only | **Not** used to train or initialise the AEO policy; no domain transfer claimed |
| Profound External API | OpenAPI 0.60.2 at build; the live `https://api.tryprofound.com/openapi.json` now reports a git-SHA version (`d8b349a8...`), re-checked 2026-10-02: every endpoint/field/enum the connector uses still exists | Commercial API (key required) | Observability substrate | Connector built from the published spec; Live Profound validation: VERIFIED with active key (7/8 surfaces live_verified, 488 live signals ingested) |
| FastAPI, Starlette, Pydantic, SQLAlchemy, Alembic, asyncpg/psycopg, arq, redis-py, httpx, structlog, networkx, numpy, scikit-learn, LightGBM, trafilatura, BeautifulSoup, lxml, sse-starlette | see `backend/pyproject.toml` | MIT / BSD / Apache-2.0 | Runtime | |
| Nuxt, Vue, Pinia, vue-router, vitest, openapi-typescript | see `frontend/package.json` | MIT | Frontend | |
| PostgreSQL + pgvector image (`pgvector/pgvector:pg16`), Redis 7 | | PostgreSQL / pgvector license; Redis 7 BSD-3 | Infra | pgvector is available but not used by any feature yet |

## Research and architectural inspiration

AEO SRE was informed by studying existing open-source and hackathon projects, research papers and engineering patterns.
These were read for ideas only; they are not dependencies, none is distributed with AEO SRE, and no license obligation
arises from studying them. The AEO-specific architecture and the implementation were developed for this project.

| Area | What was studied (ideas only) | Where it landed here |
|---|---|---|
| Incident-response systems (BlackBox, CampaignPilot) | forward-only incident stages; evidence checked before a root cause is accepted; layered root-cause analysis; detector registries | `incidents/state_machine.py`, `investigation/evidence_gate.py`, `incidents/detector.py`, `investigation/rca.py`, all written independently |
| Approval and workflow systems (LaunchPilot, SLAP, Recoil AI) | approval as a durable record; structured analysis output; operator-gated change handoff | `services/approvals.py`, experiment ledger, intervention packages |
| Provenance and web research (Lattice, Info-Ninja) | derived values tied to inputs; staged pipelines; TTL caching and progress events | `evidence/provenance.py`, `connectors/web/` |
| Experimentation and off-policy evaluation | contextual bandits (LinUCB), IPS/SNIPS/doubly-robust estimators | `policy/`, `learning/`, validated against `obp` (above) |

Repository URLs, commit SHAs and license findings for each studied project are kept as internal research notes in
[docs/notes/research-inspiration.md](docs/notes/research-inspiration.md). Per-topic working notes are under `docs/notes/`.
