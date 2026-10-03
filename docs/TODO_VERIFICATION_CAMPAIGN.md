# TODO — Milestone Verification, Repair & Final Integration Campaign (QUEUED)

Status: **done for everything that does not need a live credential or a local browser.** The 2026-10-02 verification pass recorded milestone evidence in `Milestones.md` and `Progress.md`. Still blocked: a Profound key, a model key, Docker from this user, and a browser (`libasound.so.2`). Do not treat this file as an unstarted feature list.

Source of truth to read first: Plan.md, UI.md, Milestones.md, Progress.md, Decisions.md (+ README, THIRD_PARTY.md, docker-compose.yml, .env.example, backend/, frontend/, docs/). If docs and code disagree, preserve the product thesis, fix either side, record material changes in Decisions.md (supersede ADRs, never delete).

## Rules
- Trust executable evidence only (not agent summaries, file existence, mocks alone, old reports, stale processes, checkboxes).
- Evidence levels: 0 absent · 1 scaffolded · 2 unit verified · 3 integration verified · 4 live verified · 5 closed-loop verified. Never claim 4 on mocks.
- GitHub is NOT core. Core works with ManualExecutor only; no GITHUB_TOKEN required; GitHub stays an optional adapter, tested separately, never advertised as live when mocked.
- Keep X-Actor trusted-actor for hackathon; document that production needs real auth. No full auth work.
- Never print/commit secrets; `.env` and `references/` never staged. No secret-history rewrite unless a real leak is found (then report).
- Never weaken the evidence gate to make tests pass.
- Prefer a few high-authority agents (milestone auditor, DB/migration/runtime, backend+evidence gate, frontend/API contract, Profound+model integration, ML/RL, adversarial reliability, integration lead). Integration lead commits.

## Step list (do in order)
1. Read the five control files; `git status`, current SHA (baseline ≈ 398926b + later commits).
2. Build a milestone audit matrix: every exit criterion of M1–M10 → implementation → test → runtime proof → verdict + evidence level.
3. Migration/model drift: `alembic current`, `heads`, `history`, `check`, `current --check-heads`. Confirm `policy_decisions`, `PolicyVersion.source_experiment_id`, `settings` exist consistently across SQLAlchemy, migration, DB, services, schemas.
4. Fresh disposable Postgres DB → `alembic upgrade head` → app boots → tests run. Only then (and only if the dev DB `aeo` holds nothing valuable) backup → drop → recreate → upgrade → explicit dev fixtures. Never fix drift by hand-editing a DB.
5. Classify the 6 SQLite failures (real bug / test bug / SQLite-specific / fixture isolation / unsupported): 2× test_db_schema (FK not enforced → enable FK pragma in test connection or mark PG-only), 3× test_full_loop (gate unconfirmed → decide case A fixture lacks evidence / B pipeline not attaching evidence / C gate logic wrong; fix the right layer with regression tests), 1× test_profound_client duplicate `incidents.number` (find root cause: counter/fixture leakage/sequence/rollback; add regression test). Postgres is authoritative.
6. Restart stale runtimes (api, worker, frontend) on the current tree; expose commit SHA in health if trivial.
7. Regenerate frontend types from live OpenAPI (`pnpm gen:api`, no hand-patching); confirm `favorable`, dry-run/measured fields reach TS; run nuxt prepare, typecheck, tests, build using actual package.json scripts.
8. Authoritative Postgres suite + ruff (+ type checker if configured) + frontend suite. Require 0 unexplained failures.
9. Audit M1–M9 one by one with behavior checks:
   - M1 foundation: services boot, health, empty-DB migration, no secrets in Nuxt public config, `.env` untracked.
   - M2 domain: all models + PolicyDecision/Settings; enforced state machine; illegal transitions rejected; audit trail.
   - M3 Profound: separate IMPLEMENTATION COMPLETE from LIVE VALIDATED; if no key → "Live Profound validation: BLOCKED — credentials unavailable"; if key → one minimal non-destructive live call, never print the key.
   - M4 detection/priority: baselines, grouping, dedup (repeated signals ≠ duplicate incidents), noise suppression, transparent factors, no revenue claims.
   - M5 investigation/evidence DAG: provenance, hashes, snapshots, edges, support/refute, gate; investigate test_full_loop failures per case A/B/C.
   - M6 EvidenceRanker: record model type, features, datasets, split, metrics, artifact path, inference integration, limitations; deterministic inference test; confirm investigation actually calls it.
   - M7 bandit: LinUCB/Thompson, encoder, constrained actions, `observe` legal, priors, versioning, persisted decisions (context, version, candidates, scores/probabilities, selected, timestamp), deterministic tests.
   - M8 Intervention Approval + Experiment Activation: propose → inspect → approve/reject/modify → experiment activated with exact action snapshot → verification starts; works without GitHub.
   - M9 verification/reward: dry-run never rewarded; observation inside Profound lag window rejected; after_metrics persisted; awaiting_verification → verified → rewarded; reward from measured components; policy update only after legit reward; read what test_false_success_hunt.py really asserts.
10. M10 gates:
    - A schema/runtime integrity · B test reconciliation · C real integration activation (Profound first, then model provider with unknown-evidence-ID rejection, then optional executors) · D golden path.
    - Golden path (live, current time): real Profound observation → Signal → incident if warranted (no incident is a valid recorded result) → priority → investigation (public web + Profound) → evidence DAG → structured hypothesis → gate → candidates → bandit decision → human approval → experiment activation → stop honestly at `awaiting_verification`.
    - Delayed loop proven separately with clearly labeled test data: valid post-window observation → verification (favorable/unfavorable/neutral) → reward → policy update.
11. Frontend/SSE: verify incident detail shows real backend state only (no fake numbers/graph/log); real investigation stream opens, orders events, reconnects acceptably, reaches terminal state, handles backend errors; no WebSockets.
12. Capability reporting from cheap real checks, not env-var presence only: profound, model_provider, evidence_ranker, bandit, manual_executor, github_executor.
13. `.env.example`: core vars (DATABASE_URL, REDIS_URL, PROFOUND_*, MODEL_*, NUXT_PUBLIC_API_BASE_URL), optional executors commented.
14. Security sweep: no secrets via API, CORS intentional, no debug endpoint leaks, no arbitrary shell/filesystem exposure, external mutation needs approval, references/ not staged.
15. THIRD_PARTY audit: for any directly reused code verify repo, SHA, license, files, modifications, attribution; reimplement anything unlicensed/restricted.
16. Obvious perf only: N+1 in incident detail, repeated URL fetches, unbounded SSE buffers, duplicate Profound calls, blocking sync HTTP in async handlers. Logging check: structured logs for detection, investigation, Profound, web fetch, hypotheses, gate, policy, approval, activation, verification, reward, failure; never log keys/auth headers.
17. Adversarial false-success hunt (one agent): dry-run, missing after metrics, early/duplicate/stale/wrong-experiment observation, failed/rejected intervention, unconfirmed hypothesis, missing evidence, LLM citing nonexistent evidence, missing policy version, manual executor not activated, Profound/model unavailable → system must answer unknown/pending/unconfirmed/failed/unavailable, never fabricated success.
18. Ten invariants to cover with tests: (1) no confirmation without evidence (2) no activation without valid approval (3) dry-run can't reward (4) pre-lag observations aren't outcomes (5) unknown data never fabricated (6) decisions reproducible from persisted context+version (7) experiments keep before-state + provenance (8) reward needs legitimate after-state (9) `observe` legitimate (10) works without optional executors.
19. Docs from evidence only, after verification: Milestones.md (downgrade failed ones to IN PROGRESS/BLOCKED), Progress.md (what works / live / mocked / blocked / tests / integrations run / next action; IMPLEMENTED vs TESTED vs LIVE VERIFIED), Decisions.md (supersede the GitHub-first ADR: "AEO SRE's core intervention lifecycle is executor-neutral…"), README.
20. Commit in coherent pieces (fix(migrations), fix(evidence), fix(api), chore(frontend) types, test(reliability), docs(progress)).

## M10 complete only when
Runtime: backend/frontend/Postgres/Redis/worker start clean. DB: fresh migrate to head; `alembic check` clean; current DB at all heads; policy_decisions/source_experiment_id/settings accounted for. Backend: Postgres suite + lint pass. Frontend: types regenerated, typecheck, tests, build pass. Product: lifecycle, investigation, graph, gate, interventions, policy, approval, activation without GitHub, verification, reward safeguards, learning path. External: Profound and model provider validated live if credentials exist, else recorded as BLOCKED. Reliability: false-success tests pass, degraded modes honest, stale runtime gone, SSE verified.

## Final report format
1 Milestone verification (per milestone verdict + evidence) · 2 Repairs · 3 Test evidence (commands + results) · 4 Live integrations (Profound / Model provider / GitHub = LIVE|MOCKED|BLOCKED|OPTIONAL|NOT CONFIGURED) · 5 Database state (head, current, check, fresh migration) · 6 Frontend state (types, typecheck, tests, build, SSE) · 7 Known remaining failures · 8 Final commit SHA · 9 One next highest-leverage action.

After it passes: "live product hardening" (real Profound usage, real target domain, deployment, UI polish, presentation path) — not a feature explosion.
