# Experiment verification runbook

## When an experiment becomes eligible

After a **real** execution (including manual record), the verification window opens at:

`executed_at + VERIFICATION_DELAY_HOURS` (default 48h, Profound lag).

Observations count only if `observed_at >= verification_window_start` and after execution.

## Check status (read-only)

```bash
cd backend && .venv/bin/python -m app.devtools.experiment_status <experiment-uuid>
```

Shows status, window bounds, eligibility at current UTC, before/after metrics, reward presence.

## Attempt verification

Use the API/worker path (`verify_experiment` job) or domain `evaluate()` — never bypass temporal rules via CLI.

```bash
make verify-experiment ID=<uuid>   # when implemented; must still enforce backend gates
```

Before the window opens, `after_metrics` and `reward` remain empty by design.

## Outcomes

- **Verified**: qualifying Profound observation persisted as `after_metrics`
- **Rewarded**: separate `ingest_reward` step after verified (policy update once)
- **Inconclusive**: verified but reward semantics may mark inconclusive
- **Still awaiting**: no qualifying observation — do not fabricate metrics

## Fixture experiment (current dev)

Experiment 1 on the dev fixture remains `awaiting_verification` until **2026-10-04T20:29:43Z** and real post-window observations.
