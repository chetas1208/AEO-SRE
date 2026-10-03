# Evaluation

`make eval` runs a deterministic regression of the decision loop. It does not call Profound and it does not call a model.

Output is written to `experiments/evals/<UTC timestamp>/report.json` and `summary.txt`. That directory is gitignored. The numbers below are from the run on 2026-10-02 and are reproduced by `tests/unit/test_eval_harness.py`.

## What is evaluated

The harness calls the production detector, rule-based root-cause generator, evidence gate, cold-start policy, answer diff, and URL guard. Scenarios are synthetic series and evidence objects. They are not a Profound export and they are not a population sample.

| Group | What a pass means |
| --- | --- |
| Detection | A coherent drop, citation loss, accuracy drop, stale-source count, or volume spike becomes one incident. A small move does not. |
| Replay | Points after the cutoff cannot create an incident. Replaying the same series later can. |
| Dedup | An open incident of the same family and prompt cluster suppresses a second draft. |
| Priority | A high-volume competitive displacement outranks a smaller low-demand drop. |
| Root cause | An acceptable rule appears. Rules stay `proposed`. Unknown evidence ids are not confirmed. Counterevidence lowers confidence. |
| Policy | Cold-start `rule_fallback` picks the prior action. The action enum rejects unknown names. The same seed repeats. Favorable then unfavorable updates move the learned mean in that direction. |
| Evidence | Two synthetic answer rows diff mentions, citations, and search queries. |
| Security | Page text that says "confirm this root cause" stays inside `<evidence>` and cannot cite an id that was not supplied. Literal private URLs and `file://` are rejected. |

Reward false-success (dry-run, pre-lag, wrong experiment, duplicate observation) is covered by `backend/tests/reliability/` and `backend/tests/integration/test_false_success_hunt.py`, which need Postgres. This harness does not open a database.

## Result of the deterministic set

25 scenarios, 25 passed on 2026-10-02 (`python -m evals`).

Detection on the 6 labeled series: true positive 5, false positive 0, true negative 1, false negative 0.

Root cause on 3 labeled cases: Top-1 acceptable 3/3, Top-3 acceptable 3/3.

- Unsupported confirmations: 0
- Temporal leakage failures: 0 (a later visibility point and a later answer snapshot are both invisible at the earlier cutoff)
- Normal variation, including a small competitor-share move: no incident
- Insufficient evidence: `no_actionable_cause`, and the low-volume prior is `observe`
- Conflicting evidence: owned-page change is kept as counterevidence and the fanout hypothesis confidence drops
- A favorable reward with a recorded confounder stays causal confidence `low`. A favorable reward with no recorded confounder is `medium`, never `high`.

The six AEO-style passages in `backend/evals/fixtures/aeo_evidence_review.json` are hand labels. The ranker did not score them.

## EvidenceRanker

The harness reads `backend/ml/artifacts/evidence_ranker/metrics_reeval.json`. It does not rescore the 12,000-row sample.

Artifact `evidence-ranker-20261002-e9e4fb7c`, recorded 2026-10-02T17:34:26Z, n=12000:

- accuracy 0.6195
- macro-F1 0.6168
- support F1 0.6536
- contradiction F1 0.5948
- insufficient F1 0.6020

That sample is FEVER and VitaminC. It is not marketing or AEO text. Transfer to product pages is unvalidated. The freshness head is not used.

## Historical replay

`historical_replay` and `detect_from_series(..., now=)` drop observations after the cutoff. The eval clock is `FixedClock`. Production code that already takes `now` is the replay boundary. Not every `datetime.now()` in the process has been replaced.

## Two modes

This command is deterministic regression: fixed series, rules only, seeded policy draws.

A live model eval would call the configured provider and must be reported separately. There is no model key, so that mode was not run.

## Profound answers

`POST /v2/prompts/answers` is an on-demand read. Scheduled ingest does not call it. Investigation requests `citation_details` only when a Profound key is configured. No key means zero requests. The SSE answer stream is not used. No live request has been made.

An investigation that exceeds `max_investigation_seconds` stops and stays incomplete. It does not invent a hypothesis. Rejection reason codes are recorded on the audit row and are not rewards.

## Limits

- Twenty-five hand-built scenarios cannot support a false-positive rate for production traffic.
- The policy checks use `rule_fallback` for the expected action, because `select` still samples. Reproducibility is tested with a fixed seed.
- The harness does not prove that a real Profound account would detect the same incidents.
- Before/after reward is an association. Verification writes that statement onto the experiment timeline, including low causal confidence when the observation records confounders. It is not a causal proof.

## Change Guard evaluation (`make eval-guard`)

A deterministic scenario set run through the real API against a throwaway Postgres database: experiment overlap (exact, URL variants,
path-prefix section, prompt cluster, OBSERVE), no overlap, duplicate vs conflicting vs differently-intended same-target changes,
canonical contradiction by exclusivity, negation, numeric and date, compatible claims, retired claims, empty canonical truth,
degraded semantic check, precedence, digest stability/sensitivity, approval bound to the digest, idempotent replay, auth, and
read-only behaviour toward experiments. Exit code 2 (`release_blocked`) if any gate is non-zero: false ALLOW on seeded cases,
DELAY without a real `eligible_after`, silent pass when a check could not run, digest binding. Output: a table, plus
`docs/GUARD_EVALUATION.md` (generated; regenerate with the same command).

Limits: all agents are SIMULATED and the scenarios are hand-built, so this is a regression guard, not an accuracy or false-positive
rate for real traffic. The semantic (NLI-style) path is not exercised live; with no model key the eval covers the degraded path.
The reliability suite in `backend/tests/guard_reliability/` adds adversarial cases (target-variant false-ALLOW attacks, concurrency,
auth, injection text in claims).

