# Research inspiration (internal notes)

Projects studied while designing AEO SRE. Studied only: read-only clones under the gitignored `references/` directory,
not distributed, not imported by production code, no code copied. Provenance framing: Decisions.md DEC-040.

Classification evidence (2026-10-03): normalised-line and 3-line-shingle comparison of each clone against `backend/app`,
`backend/ml`, `backend/tests`, `backend/migrations`, `frontend/{components,pages,composables,utils,stores,layouts}` and
`scripts/`. Remaining overlaps are import blocks, the standard Alembic `env.py` template and one-line framework idioms
only. Production code, Makefile, docker and pyproject contain no path or import referring to `references/`.

| Project | Commit SHA | License at that SHA | Classification | What was learned (reimplemented independently where used) |
|---|---|---|---|---|
| [alejandro-publius/blackbox-datahub](https://github.com/alejandro-publius/blackbox-datahub) | `b72a32c64e11404b1de98b517bde7e487ca35963` | Apache-2.0 | Algorithm/idea reimplemented independently | forward-only stage progression; evidence gate listing every failed requirement; evidence vs hypothesis separation. Its GitHub-PR repair flow was not adopted. |
| [EZZEASY/CampaignPilot](https://github.com/EZZEASY/CampaignPilot) | `0502103dd815e6c65a666176e6eab89915828f57` | MIT (c) 2026 EZZEASY | Algorithm/idea reimplemented independently | detector registry with uniform alert shape; layered root-cause protocol; query data before concluding. |
| [1300Sarthak/mcp-hackathon-sf](https://github.com/1300Sarthak/mcp-hackathon-sf) (Info-Ninja) | `ee2bfea80c811c1a41844e0aae69e2d7d654a413` | none found | Research / inspiration only | TTL result cache, stage-progress events, multi-source fan-out. |
| [atkamel/launchpilot](https://github.com/atkamel/launchpilot) | `9fd85c1a6fcea11503e5c27f24cd6b6b37a11c4f` | none (all rights reserved) | Algorithm/idea reimplemented independently | approval as a durable row with requester/decider; approve/reject REST shape. |
| [WeltonWang02/spreadsheet_hackathon](https://github.com/WeltonWang02/spreadsheet_hackathon) (Lattice) | `535ffb7eaf771fee8aeeef6c5b21d5bc63f8e1af` | none | Research / inspiration only | staged pipeline with inspectable outputs; hashing inputs for provenance. |
| [mogonlac/hackupc2026](https://github.com/mogonlac/hackupc2026) (SLAP) | `89eb5b3f66d9fda07ed5ef16c750e220b05ec5d7` | ambiguous ("HackUPC 2026 License" undefined) | Research / inspiration only | LLM structured edit followed by human-gated change. |
| [A-Piyas-04/Recoil-AI](https://github.com/A-Piyas-04/Recoil-AI) | `a65f14fa2a3b6e8a9d687f31d6008678772c16b3` | none | Research / inspiration only | grounded-generation instruction; risk decomposition before approval. |

No repository was classified "direct third-party code present". Detailed per-project notes: `blackbox.md`,
`campaignpilot.md`, `info-ninja.md`, `launchpilot.md`, `lattice.md`, `slap.md`, `recoil.md`, `third-party-a*.md`.
