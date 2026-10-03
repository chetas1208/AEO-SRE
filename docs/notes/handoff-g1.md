# Handoff G1: Change Guard backend core

**API STABLE** (shapes below are final; G3 may build against them. Regenerate types with `cd frontend && pnpm gen:api`.)

Spec: `docs/CHANGE_GUARD_SPEC.md`. Code: `backend/app/changeguard/` (`service.py` orchestration, `digest.py`, `targets.py`,
`overlap.py`, `decision.py`, `semantic.py` adapter for G2's `claims.py`/`contradiction.py`, `canonical.py`),
`backend/app/models/changeguard.py`, `backend/migrations/versions/0004_change_guard.py`, routes `api/routes/change_checks.py`
and `api/routes/canonical_claims.py`, schemas `schemas/changeguard.py`. Tests: `backend/tests/changeguard/`.

## Endpoints

| route | auth | notes |
|---|---|---|
| `POST /api/change-checks` | `Authorization: Bearer $CHANGE_GUARD_TOKEN` | 201 new decision, 200 + `replayed: true` for same idempotency key + same proposal digest, 409 `CHANGE_CHECK_KEY_CONFLICT` for same key + different digest. `recheck: true` in the body evaluates again (new decision, new digest) instead of replaying. |
| `GET /api/change-checks` | same bearer token | filters `org_id`, `decision`, `target` (normalized, prefix), `experiment_code`, `source_mode`, `origin`; `limit` 1..200, `offset`; newest first. **The UI must not call it** (no token in the browser): it reads `protection` and `change_guard` instead. |
| `GET /api/change-checks/{id}` | same | one stored check |
| `GET/POST /api/organizations/{id}/canonical-claims`, `PATCH .../{claim_id}` | `X-Actor` (named human; `system`/`model`/`agent:*` actors are refused with 422 `CANONICAL_CLAIM_INVALID`) | `PATCH {"status":"retired","retire_reason":..}` soft-retires (final; the key becomes reusable). Fields: `key, statement, entities[], scope, valid_from, source` (provenance), `status active|retired`, `created_by/updated_by/retired_by/retired_at`. `GET ?status=retired`, paginated. |
| `GET /api/experiments/{id}` | none | gains `protection` (below) |
| `GET /api/incidents/{id}/interventions`, `GET /api/interventions/{id}` and every action response | none | `InterventionOut.change_guard` (below), null = never checked ("unavailable") |
| `POST /api/interventions/{id}/change-check` | none (internal, UI) | (re)runs the guard on the current proposal and returns `ChangeGuardVerdict`; use it for a "Refresh check" button, or when `change_guard` is null/`stale` |
| `POST /api/interventions/{id}/approve` and `/modify` | none | body gains `review_reason` (string) |

Token unset: every `/api/change-checks*` route answers **503 `CHANGE_GUARD_NOT_CONFIGURED`**; wrong/missing token **401 `CHANGE_GUARD_UNAUTHORIZED`**; more than `CHANGE_GUARD_RATE_LIMIT_PER_MINUTE` (default 120) requests/min per client address **429 `RATE_LIMITED`**
(`details.retry_after_seconds`). Body limit 1 MiB (413 `PAYLOAD_TOO_LARGE`), plus field limits (claims <= 100 x 1000 chars, text 50k, diff 100k).

## ChangeSet in (POST body)
`org_id` or `org_domain`; `agent {id, name}`; `profound_run_id?`; `source_mode` **LIVE|SIMULATED** (required); `target_url?`;
`action_type` (ActionType vocabulary); `proposed_claims[]`; `proposed_text?`; `proposed_diff?`; `reason`; `expected_kpi?`;
`risk? low|medium|high`; `reversible?`; `idempotency_key?` (default `agent_id:run_id:proposal_digest`); optional
`prompt_cluster_ids[]` / `prompts[]` (what prompt clusters the change addresses, used for check 1); `recheck`.

## ChangeCheckOut (response)
`id, change_set_id, org_id, decision (ALLOW|MERGE|DELAY|REQUIRE_REVIEW|BLOCK), findings[], eligible_after, merged_proposal,
semantic_check (ok|degraded|skipped_no_canonical_truth), guard_version ("change-guard/1.0"), digest (action digest, 64 hex;
approvals bind to it), proposal_digest, replayed, agent {id,name}, source_mode, origin (external|intervention), target_url,
target (normalized), action_type, profound_run_id, idempotency_key, experiment_codes[], evaluated_at, created_at`.

Finding: `{type, check (1|2|3), decision, severity (block|delay|review|merge|info), reason, references{...}, eligible_after, details{...}}`.
Types: `active_experiment_contamination` (check 1; references `experiment_id, experiment_code, incident_id`; details
`target_overlap_pct, prompt_cluster_overlap_pct, cluster_overlap_basis (cluster_id|prompts|topic_mention), matched_targets[],
protected_targets_total, experiment_status, experiment_action, observe_baseline, eligible_after_basis, window_start, window_end`),
`duplicate_target_change` (MERGE), `conflicting_target_change` (REQUIRE_REVIEW; references `change_set_id` or `intervention_id`,
`agent_id`; details `target_match exact|prefix, other_action, claims_relation`), `canonical_conflict` (BLOCK; references
`canonical_claim_id, canonical_claim_key`; details `proposed_claim, canonical_statement, relation, reasons, confidence, source`),
`canonical_uncertain` (REQUIRE_REVIEW), and informational `decision: ALLOW` findings: `canonical_truth_unavailable`,
`semantic_check_degraded`, `no_claims_to_check`. **Every** finding is returned, not only the winning one. `eligible_after` (top level)
is the latest DELAY time; it can be null only when verification is overdue (finding detail `eligible_after_basis = verification_overdue`).

## `eligible_after` semantics (window service, `app/experiments/window.py`)
Window not open yet: the window start (`verification_window_start`, the "eligible_at" the experiment page shows). Window open,
outcome not measured: the window end. `executing` with no window yet: now + `verification_delay_hours` (earliest possible start).
Nothing else is ever invented. Protecting statuses: `executing`, `executed`, `awaiting_verification`, real executions only (dry runs and
other orgs never protect). OBSERVE experiments protect their owned evidence pages and are flagged `observe_baseline: true`.
Fixture EXP-0001 is OBSERVE on org auth0.com: a ChangeSet for `https://auth0.com/enterprise` (one of its 5 owned pages, 20% target overlap)
returns DELAY with `eligible_after = 2026-10-04T20:29:43.187727Z`.

## `protection` on the experiment detail
`{protected: bool, until: datetime|null (eligible_after), until_basis, targets[] (normalized pages), checks_blocked_count, recent_checks[] (max 10)}`;
`recent_checks[] = {id, change_set_id, decision, agent_id, agent_name, source_mode (LIVE|SIMULATED), origin, target, action_type, reasons[], eligible_after, created_at}`.
Show a SIMULATED badge when `source_mode == "SIMULATED"`. `protected: false` once the experiment is verified/rejected/failed.

## `change_guard` on interventions (AEO SRE's own proposals)
`{check_id, decision, findings[], eligible_after, merged_proposal, semantic_check, guard_version, digest, evaluated_at, stale, blocks_approval, requires_review_reason}`.
The guard runs when the intervention is proposed (pipeline) and again at approve. The UI never decides: `blocks_approval` (BLOCK/DELAY; never true for
`observe`) disables Approve and the findings explain why; `requires_review_reason` (REQUIRE_REVIEW) shows a warning and a required text box sent as
`review_reason`; MERGE shows `merged_proposal`; `stale: true` means the proposal changed since the check (call `POST .../change-check`).
Approve/modify refused: **409 `CHANGE_GUARD_BLOCKED`**, `details {decision, check_id, findings, eligible_after, requires_review_reason, digest}`. DELAY/BLOCK cannot be overridden.
A REQUIRE_REVIEW approve without `review_reason` (>= 3 chars) is the same 409 with `requires_review_reason: true`.

## Approval digest binding
`approvals.action_digest` is set with the decision (digest of the exact effective change + the experiment context at check time). Activation,
manual package issue, GitHub execution and `POST .../executed` all pass `require_executable_approval`, which recomputes the digest from the current stored
change; any edit of `proposed_change` after approval -> **409 `APPROVAL_DIGEST_MISMATCH`** (`details.approved_digest/current_digest`). `observe` (no approval)
is compared with the guard record made when it was proposed. Approvals created before Change Guard have no digest and are allowed with a warning log.

## Events
Audit rows: `change_check.created`, `change_check.decided` (entity `change_check`), `canonical_claim.changed` (entity `canonical_claim`, actor = X-Actor),
`intervention.approval_refused`. SSE: a persisted incident event with `event_type: "change_check.decided"` is emitted on the incident of each experiment a check
touched (metadata `check_id, decision, agent_id, source_mode, target, eligible_after`): refetch the experiment panel on it. `canonical_claim.changed` is audit only
(there is no incident to attach an SSE event to).

## Persistence (migration 0004)
`change_sets` (immutable, unique `(org_id, idempotency_key)`, `proposal_digest`), `change_checks` (append-only, one row per evaluation, `action_digest`, findings JSON),
`canonical_claims` (partial unique active key per org, retire not delete), `approvals.action_digest`. RESTRICT FKs, CHECK constraints for decision/source_mode/semantic state.
ORM guards plus Postgres triggers refuse UPDATE/DELETE on `change_sets`/`change_checks`.

## Decisions and honest limits
- "Pending" external ChangeSets (check 2) = created within `CHANGE_GUARD_PENDING_TTL_HOURS` (72) whose latest decision is ALLOW or REQUIRE_REVIEW; agents do not report completion, so TTL is the only expiry. BLOCK/DELAY/MERGE decisions are not pending (they were told to stop or fold in).
- AEO SRE's own pending interventions (selected, incident awaiting approval/approved, non-observe) are compared on the same target.
- Cluster overlap for external ChangeSets is explicit (`prompt_cluster_ids`, `prompts`) or inferred from the cluster topic named in claims/text (`topic_mention`, reported as inferred).
- Bare site root is not a prefix of the whole site (root only matches root).
- Check 3 uses G2's `evaluate_canonical`; with no model/ranker the check reports `semantic_check: degraded` (rules decided), which is the normal state in this dev environment.
- Rate limiter is in-process (one API process).
- `GET /api/change-checks*` need the token (agents' proposals are not public); the UI reads `protection`/`change_guard`.
