# Handoff G2 (claims + canonical-truth contradiction, Change Guard check 3)

Status: done. `cd backend && .venv/bin/python -m pytest tests/changeguard_claims -q` -> 39 passed; ruff clean. No git operations, no DB, no frontend.

## Files
- `backend/app/changeguard/claims.py` (extraction), `contradiction.py` (rules + semantic escalation + `evaluate_canonical`)
- `backend/app/connectors/llm/registry.py`: appended `claim_relation_classifier_v1` (purpose CLASSIFICATION). No model extraction prompt added: extraction is deterministic only (existing `claim_extractor_v1` untouched/unused).
- `backend/tests/changeguard_claims/`: `corpus.jsonl` (SYNTHETIC, 90 pairs), `corpus_eval.py` (metrics; `python -m tests.changeguard_claims.corpus_eval` runs rules / +real ranker), `test_claims.py`, `test_contradiction.py`, `test_corpus.py`.
- `backend/app/changeguard/__init__.py` created only if absent (G1 may own it).

## Public interface for G1 (synchronous, pure)
```python
from app.changeguard.claims import extract_claims, parse_claim, canonical_from_mapping, Claim
from app.changeguard.contradiction import evaluate_canonical, compare_claims, EvaluationResult, Finding, Relation

proposed = extract_claims(change_set.proposed_claims)        # list[str] -> one Claim per string, as-is; or free text -> per sentence
canon_rows = [...]                                            # Claim | mapping {id|key, statement|text, status, valid_from, valid_until, scope, entities}
res = evaluate_canonical(proposed, canon_rows, gateway=gw, ranker=ranker, now=None)
res.as_dict()  # JSON-safe
```
- `EvaluationResult`: `findings[]`, `semantic_check` (`ok|degraded|skipped_no_canonical_truth`), `pairs_compared`, `model_used`, `ranker_used`, `ranker_degraded`, `ignored_canonical` ({retired, not_yet_valid, expired} counts), `unresolved_pairs`, `degraded_reasons` (e.g. `no_gateway`, `gateway_not_configured`, `model_failed`, `no_ranker`, `ranker_degraded`, `unresolved_ambiguous_pairs`), `pair_relations` (audit; non-UNRELATED pairs).
- `Finding`: `type` (`canonical_conflict|canonical_uncertain`), `severity` (`high|medium`), `proposed_claim_id`, `canonical_claim_id`, `relation`, `reasons[]`, `confidence`, `source` (`rules|ranker|model`). Suggested mapping: any `canonical_conflict` -> BLOCK; only `canonical_uncertain` -> REQUIRE_REVIEW; `skipped_no_canonical_truth` -> report, never pass silently. For `canonical_uncertain`, `relation` is the SUSPECTED relation (CONFLICTING); the type is what matters.
- `gateway`: any `ModelGateway`; the sync twin (`gateway.sync()`) is used, health must be READY. From async code call via `asyncio.to_thread(evaluate_canonical, ...)`. `ranker`: `EvidenceRanker` (instance passed in; never auto-loaded here). `ranker=None` or `gateway=None` => rules only and `semantic_check="degraded"`.
- `Claim.id`: pass `Claim`/mapping with `id` to control ids (G1 should pass canonical row ids); otherwise `clm_<sha256(normalised text)[:10]>`, made unique within a list.
- Canonical rows with `status` retired/inactive/archived/deleted, `valid_from` > today, or `valid_until` < today are ignored and counted; if nothing is left -> `skipped_no_canonical_truth`.

## Rules (compare_claims)
Same `subject` (normalised feature/resource: saml, sso, okta, scim, seats, price, storage ... via configurable `SynonymTable`, `DEFAULT_TABLE.extend(...)`):
- availability sets: affirm on plans P => has {P}; `exclusive` (only / exclusive to / Enterprise-only / limited to) => also NOT on every other known plan; negate => not on P (or ANY). Clash (have ∩ not) => CONFLICTING, reason `exclusivity_vs_inclusion` if either side exclusive else `negation_mismatch`; "not exclusive to X" vs exclusive X also conflicts.
- numbers (same unit): exact/max/min intervals; same-kind different value, or disjoint => `numeric_mismatch`; if plan scope missing on a side add `scope_mismatch` and confidence 0.75. Prices keep unit (`usd/seat/month`), no period conversion.
- dates (same role since/until/on): differing at the shared precision => `date_mismatch` (2026 vs 2026-03 compatible).
- No conflict: DUPLICATE (same scope/polarity/numbers/dates), COMPATIBLE (proposed adds nothing), DEPENDENT (proposed adds plans/numbers/dates/exclusivity not in canonical; `scope_mismatch`). Different subjects => UNRELATED (0.9).
- Ambiguous (`needs_semantic`): related subjects (saml~sso~okta...), unparsed subject with lexical overlap, add-on qualifier mismatch.

## Precedence (tested)
Deterministic CONFLICTING is final. Definitive non-conflict rules results are final: model/ranker are not even consulted. For ambiguous pairs only: model CONFLICTING => `canonical_conflict` only if lexical/entity overlap AND the non-degraded ranker does not say support >= 0.75; otherwise `canonical_uncertain` (reasons `model_conflict_without_overlap` / `model_conflict_vs_ranker_support`). Ranker contradiction (>= 0.55) alone => `canonical_uncertain` only (ranker is ~62% accurate); a model COMPATIBLE never clears a ranker-raised concern. Model output: structured (`claim_relation_classifier_v1`), claim text only in the `<untrusted_data>` block, ids validated against the supplied pairs (unknown/duplicate id rejects the whole response; 2 attempts; transport failure => degraded, rules findings kept), <= 10 pairs per call, <= 60 escalated pairs.

## Measured (SYNTHETIC corpus, 90 pairs: 34 CONFLICTING, 16 DUPLICATE, 11 COMPATIBLE, 13 DEPENDENT, 16 UNRELATED; 31 hard conflicts)
Honesty note: I wrote the corpus first, then relabelled 9 pairs where my initial label contradicted the class definitions and made one rule fix (numeric limits compared regardless of polarity) after looking at failures. The rule numbers are therefore an optimistic regression gate, not out-of-sample accuracy.
- Rules only: 5-class accuracy 0.967 (87/90). CONFLICTING precision 1.00, recall 0.912 (31/34). Hard conflicts (exclusivity/negation/numeric/date/adversarial): 31/31 caught, zero false ALLOW, also when run through `evaluate_canonical` with no gateway/ranker. The 3 misses are the deliberately semantic non-hard pairs (premium add-on wording, "contact sales" vs self-serve, EU-only vs US hosting): rules say COMPATIBLE/UNRELATED definitively.
- Rules + real EvidenceRanker (lex_cos, not degraded): no change (31/34, P 1.00): the misses are definitive rule results, so they are never escalated. Raw ranker alone on every pair: conflict P 0.83, R 0.29 (10/34), consistent with its ~62% 3-class accuracy; it labelled all 3 semantic conflicts `insufficient`.
- Live model smoke (claude-haiku-4-5 through ModelGateway, 10 hand-picked corpus pairs incl. the 3 semantic misses and an injection pair, one successful call): 10/10 matched labels, structured output valid. Earlier attempts failed because the default CLASSIFICATION `max_output_tokens=512` truncated the JSON; the request now sets `max_output_tokens=160*pairs+128` and asks for <=12-word rationales. ~5 live calls total, key never printed. Tiny sample: not an accuracy claim. The rest of the model path is tested with fake gateways and one respx-mocked HTTP test.
- Prompt injection tests: injected text does not change rule outcomes; sits only in the untrusted block (never system/input); a model COMPATIBLE cannot clear a deterministic conflict; unknown ids and an invented canonical id are rejected.

## Limitations
- English marketing phrasing only; one claim per sentence; subject = first recognised feature; unknown synonyms need `SynonymTable.extend`. "Business" as a generic word is read as the plan.
- Rules treat definitive COMPATIBLE/UNRELATED as final, so semantic conflicts phrased with unseen vocabulary (the 3 misses) are not escalated. Option for G4/G1 if wanted: always run the model on pairs with lexical overlap even when rules say COMPATIBLE (would need a policy decision against the spec's "model can only raise when rules do not say COMPATIBLE high confidence").
- Price periods are not converted (monthly vs annual are different units => no conflict, treated as DEPENDENT); quarters (Q3 2026) not parsed; unscoped numeric diffs conflict at 0.75 (conservative).
- `evaluate_canonical` is synchronous and blocks on the model; the gateway `health()` is the process-level status, not a fresh probe.
