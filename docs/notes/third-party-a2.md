# Third-party notes - A2

| field | value |
|---|---|
| Repo | https://github.com/alejandro-publius/blackbox-datahub |
| Commit SHA | b72a32c64e11404b1de98b517bde7e487ca35963 |
| License | Apache License 2.0 ("Copyright 2026 BlackBox contributors"); no NOTICE file |
| Code reused (copied) | **No.** No source lines, identifiers beyond generic terms, or SQL/DataHub logic were copied. |
| Concepts adapted | (1) forward-only, terminal-sticky stage progression -> `backend/app/incidents/state_machine.py` (rewritten as an explicit edge map with actor/reason audit records); (2) "machine-checked evidence gate lists every failed requirement; cited evidence must agree with the claim; contradictory evidence cannot confirm" -> `backend/app/investigation/evidence_gate.py` (AEO-specific EvidencePolicy, Profound/content/citation/timestamp requirements); (3) design references for A10's PR executor (branch-prefix allow-list, disabled-by-default, never-raise status dict, PR body from recorded evidence only), the SSE snapshot-on-connect pattern and eval/test patterns (see `docs/notes/blackbox.md`). |
| Attribution | In-code docstring in `evidence_gate.py` credits BlackBox as pattern lineage. Suggested `THIRD_PARTY.md` entry: "Design inspiration: BlackBox (alejandro-publius/blackbox-datahub), Apache-2.0, https://github.com/alejandro-publius/blackbox-datahub @ b72a32c. Evidence-gated root-cause confirmation and verify-before-mutate patterns; no code copied." |
| If anyone later copies code | Apache-2.0 sec. 4: include the license text, retain copyright/attribution notices, and mark modified files as changed. |
