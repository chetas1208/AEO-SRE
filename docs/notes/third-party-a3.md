# Third-party references (A3)

| Repo | SHA | License | Code reused? | Concepts adapted | Attribution |
|---|---|---|---|---|---|
| https://github.com/EZZEASY/CampaignPilot | `0502103dd815e6c65a666176e6eab89915828f57` | MIT (Copyright (c) 2026 EZZEASY); LICENSE read at that SHA | No. All code in `detector.py`, `priority.py`, `rca.py` is original. | Registry of independent detectors with a uniform alert shape and severity-sorted output; layered cross-system root-cause protocol (ad / website / product / customer / competitor levels -> our AI Engine / Citation / Owned / Competitor / External / Canonical layers); "query data first, never guess" rule; proposed-action log as the seed of an experiment ledger; fixed tool vocabulary the LLM cannot extend. | Not required (no code copied); credited here and in `docs/notes/campaignpilot.md`. If code is ever copied, keep the MIT notice. |

No other third-party code or datasets were used by A3. Runtime deps used: pydantic, structlog, SQLAlchemy (already
in the project).
