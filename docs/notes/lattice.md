# Lattice (WeltonWang02/spreadsheet_hackathon) review (A6)

- Repo: https://github.com/WeltonWang02/spreadsheet_hackathon, cloned `--depth 50` to `references/lattice`, HEAD `535ffb7eaf771fee8aeeef6c5b21d5bc63f8e1af` ("clean up", 2025-02-16). `gh search`/`gh repo view` confirm the repo exists; it has no description, so I could not confirm from metadata that this is the intended "Lattice". Its content matches a "Lattice"-style spreadsheet-as-workflow hackathon app (Next.js 15, React 19, OpenAI + a Parallel-style task API).
- **License gate: NO LICENSE file, no `license` in package.json, GitHub licenseInfo null -> all rights reserved. Concepts only; nothing copied.**

## What it actually is
A Next.js spreadsheet UI where cells/columns are LLM tasks. Files of interest:
- `src/components/WorkflowBuilder.tsx`: ordered `WorkflowStep[]` (`single | 3d | aggregation | llm_pipe`), each with `executed` flag; "Run all" executes steps sequentially, each step's output table is the next step's input (`prevRow` + `data`).
- `src/components/ThreeDSpreadsheet.tsx`: a "3D" sheet = one sub-sheet per upstream row (entity -> sub-entities), i.e. a two-level hierarchy of derived rows.
- `src/lib/agent.ts`: task create + execute against an external API with a file cache keyed by `md5(url + options)` and 24h expiry; response carries `fromCache`.
- `src/app/api/aggregate/route.ts`: LLM aggregation prompt with the first column pinned to the upstream row's name (a lineage key) and a fallback that returns empty values rather than a guess when JSON parse fails.
- `src/lib/storage.ts`: localStorage state only. `src/app/api/orchestrate/route.ts` is a keyword-matching stub.

There is no explicit provenance graph, hashing of content, or audit trail in the repo; "auditable execution graph" is only implicit in the step pipeline (steps run in order, outputs visible per step, cache-hit flag).

## Ideas worth keeping (all reimplemented from scratch)
1. Every derived value stays linked to the upstream row that produced it (lineage key). In our graph: every node/edge records `source, timestamp, confidence, extract, hash, retrieval_method`, and derived edges are flagged `derived`.
2. Hash the inputs so a step is reproducible and a changed input is detectable (their md5 of request). Ours: sha256 of normalized text per evidence row, and `evidence_snapshot()` with a `snapshot_hash` for the experiment ledger plus `snapshot_drift()`.
3. Surface "from cache / not fresh" explicitly instead of hiding it. Ours: `EvidenceStatus` (live/changed/stale/unavailable/failed) and `freshness_risk` are carried into graph nodes; failure never reads as "no evidence".
4. Prefer an honest empty over a guessed value on parse failure (aggregate route). Ours: `Provenance.missing` lists unrecorded fields; confidence along an explain path is `None` if any hop lacks confidence.

## Not adopted
Client-side localStorage state, MD5 (we use sha256), keyword orchestration, LLM-authored aggregations.

## Evidence-gating ideas from `references/blackbox-datahub` (Apache-2.0, SHA b72a32c, A2's clone)
Skimmed `backend/blackbox/models.py`: facts (machine-produced `EvidenceItem`) are typed separately from LLM `Hypothesis`, hypotheses cite `evidence_ids`, root cause cites evidence ids. We mirror this: `Hypothesis` is a distinct node role (`inference`, status `proposed` until the A2 gate confirms), persisted `contradicts` edges are surfaced in `HypothesisOut.contradicting_evidence_ids`, and `ValidationReport.warnings` flags evidence that scores as contradicting (>=0.5 and > support) but has no `contradicts` edge, so the gate cannot be fed an evidence set that silently drops conflicts.
