# Handoff G4 (Change Guard reliability, evaluation, docs)

## Delivered
- `backend/tests/guard_reliability/` (conftest, support, test_false_allow, test_canonical, test_precedence_digest, test_api_security, test_delay_window; ~108 tests). Whole directory skips (visible skip reason) while `app.changeguard.service` is not importable. Ruff clean.
- `backend/evals_guard/` (`python -m evals_guard`, `make eval-guard`): 26 scenarios, throwaway DB `aeo_evalguard_<pid>`, writes `docs/GUARD_EVALUATION.md` and `experiments/guard_evals/<ts>/report.json`. Exit 2 = release_blocked, 1 = scenario failure or environment problem (no Postgres / guard not built).
- `scripts/simulate_agent_change.py` (SIMULATED only; token from env only; `--seed-canonical`).
- Docs: `docs/CHANGE_GUARD_INTEGRATION.md`, DEC-041, Milestone 11, Progress, README section, ARCHITECTURE, EVALUATION.
- Makefile: appended `eval-guard` and added it to the `.PHONY` line.

## Assumed API shape (adapt here if G1 differs)
See `docs/notes/requests-g4.md`. All response access goes through accessors in `tests/guard_reliability/support.py` and `_decide` in `evals_guard/harness.py`. Eval seeds experiments with `tests.reliability.helpers.executed_experiment` (eval imports the tests package).

## Honesty
Profound facts in the docs come from help.tryprofound.com (Call API, Human Review) and docs.tryprofound.com (run/get run agent API). Not verified and therefore not written: Human Review limits beyond the two stated on its page, Command Center scope, "20,000 agents", Sheets parallelism. Call API variable syntax (docs say `/` picker) and whether logic nodes parse JSON are noted as unconfirmed.

## Results against G1's tree (2026-10-03 ~20:55)
- Reliability suite: 97 passed, 7 failed, 1 skipped. The 7 are real findings, filed in `docs/notes/requests-g4.md` (A-C): false ALLOW on www / percent-encoded / dot-segment / upper-case-path target variants, approval digest not persisted (APPROVAL_DIGEST_MISMATCH cannot fire), concurrent conflicting ChangeSets both ALLOW.
- `make eval-guard`: 22/24 scenarios, release_blocked (false_allow=1, digest_binding=1); delay_without_eta=0, silent_pass=0. Exit code 2. docs/GUARD_EVALUATION.md reflects that run.
- eligible_after semantics follow G1 (window start while not started, end while open); see request E.

## Not done / open
- No tunnel/deploy; no real Profound Agent call.
