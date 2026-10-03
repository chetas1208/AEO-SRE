# Requests from G4 to other owners

1. (G1) ChangeSet: please accept an optional `prompt_cluster_ids` (list of uuids) so prompt-cluster overlap can be tested and reported at the API level. `test_prompt_cluster_overlap_is_delayed` and eval scenario `overlap_prompt_cluster` skip/are marked UNSUPPORTED on a 422.
2. (G1) Response field `decision` (top-level), `digest`, `id`, `replayed`, `semantic_check`, `source_mode`, `findings[].type/severity/reason/references/eligible_after`, and a top-level `eligible_after` for DELAY: G4 reads exactly these names. Tell G4 if they differ.
3. (G1) Finding `type` strings should contain `contamination` for check 1, `duplicate`/`conflict` for check 2, `canonical` for check 3 (tests match substrings). Overlap percentages should appear in the contamination finding (key containing `overlap`).
4. (G1) Optional but useful for Profound's text-only Call API output: a short top-level `summary` string (decision + first reason + eligible_after) that an Agent step can read without JSON parsing.
5. (G1) `approvals.action_digest` column name is assumed by tests/eval (`select action_digest from approvals`).
6. (G1) Canonical claim PATCH `{"status": "retired"}` and `valid_until` are assumed.

## Findings from running G4's suite/eval against G1's current tree (2026-10-03)
Reality check: items 1-6 above were mostly satisfied by G1 (prompt_cluster_ids, action_digest, `status: retired`). Real findings:

A. False ALLOW (targets.py `normalize_url`): a protected `https://testco.example/enterprise/security` is NOT matched by
   - `http://www.testco.example/...` (www not folded),
   - `https://testco.example/enterprise/%73ecurity` (percent-encoding not decoded),
   - `https://testco.example/enterprise/./security` (dot segments not resolved),
   - `https://TESTCO.EXAMPLE/ENTERPRISE/SECURITY` (path case; B4's `target_key` lowercases the whole key, so the guard is less conservative than B4).
   Suggested: decode unreserved percent-escapes, resolve `.`/`..`, strip leading `www.`, casefold the path for matching.
B. Approval digest not persisted: `POST /api/interventions/{id}/approve` runs a guard check (origin=intervention, digest present) but `approvals.action_digest` stays NULL (log `changeguard.approval_without_digest`), so `APPROVAL_DIGEST_MISMATCH` can never fire. `bind_digest` sets the attribute; something in `decide_approval` (or refresh) loses it.
C. Race: two agents posting contradictory claims for one target concurrently both got ALLOW (no serialization of the duplicate/conflict check). Suggest an advisory lock per (org, target_key) around evaluate+insert.
D. Overdue verification: DELAY with no `eligible_after` ("verification_overdue"). Spec gate says DELAY always carries a real eligible_after. Either do not DELAY (measurement is overdue, not in progress) or document the exception; the eval gate counts only DELAYs, so the overdue case is not hit by it today.
E. Spec ambiguity: spec says "if verification has not started the eligible time is window end" but also quotes 2026-10-04T20:29:43Z for EXP-0001, which is the window START. G1 returns window start while not started, window end while open. G4 tests/eval follow G1 (matches the quoted fixture date). Lead should fix the spec sentence.
F. `valid_until` is not part of canonical claims, so "expired canonical claim" is untestable; only `valid_from` (future claim) and `retired` are covered.
G. `target_url: ""` is accepted (201, treated as no page target). Probably fine; flagged only because an empty string looks like a client bug.
