# AEO SRE — Backend Deep Engineering Campaign (shared brief for A1–A5)

Baseline: commit 5c6848a. Backend 822 passed/1 skipped, ruff clean, frontend typecheck + 34 Vitest + build, Alembic 0001 head, eval 25/25 (`make eval`). Fixture DB: 60 `dev_fixture` signals, 1 incident, 1 experiment (OBSERVE, awaiting_verification, baseline metrics set, 0 observations, 0 rewards). **Experiment 1 must NOT verify/reward before 2026-10-04T20:29:43Z and must not be altered.** Profound is `not_configured` (key arriving soon): adding `PROFOUND_API_KEY` + restart must be the only cutover step. Final model provider unknown.

## Thesis (do not drift)
OBSERVE → DETECT → PRIORITIZE → INVESTIGATE → PROVE → DECIDE → APPROVE → EXPERIMENT → VERIFY → LEARN. Profound supplies much of OBSERVE; AEO SRE owns the control loop. Not the product: GitHub (optional executor only, never required), generic content/SEO tools, chatbot, CRM, Slack/Jira/Notion, agent swarms. No new tabs/UI features, no PPO, no new ML model, no Kafka/K8s/Temporal, no live-data invention, no early verification, no manual reward, no causal claims.

## Principle
LLMs may extract, classify, summarize, generate hypotheses, draft explanations/interventions. LLMs must NOT own: state transitions, timestamps, metric arithmetic, evidence existence, authorization, reward, verification eligibility, policy versioning, DB identity, incident dedup, experiment immutability. Deterministic logic stays deterministic.

## Ownership (edit only what you own; re-read before editing others' files; minimal edits; no reformatting; NEVER git add/commit/reset — A5 commits)
| Agent | Owns |
|---|---|
| A1 Domain integrity | `backend/app/models/**`, `backend/migrations/**`, `app/incidents/state_machine.py`, state-machine/transaction/locking/idempotency code in `app/services/**`+`app/core/**` (audit, queue), `app/devtools/audit_db.py`, DB constraints/indexes |
| A2 Profound + evidence + investigation | `app/connectors/profound/**`, `app/connectors/web/**`, `app/services/ingestion.py`, `app/investigation/**` (collector, rca, evidence_gate confidence/counterevidence), `app/evidence/**` (graph/provenance), `app/incidents/detector.py`+`priority.py`, `app/devtools/profound_smoke.py`, `ingest_live.py` |
| A3 Model runtime | `app/connectors/llm/**` (ModelGateway), config for MODEL_*, `app/devtools/model_smoke.py`; may edit ONLY the LLM-call sites in `investigation/rca.py` and `interventions/llm.py` to use the gateway (coordinate with A2/A4 by minimal diffs) |
| A4 Policy/experiments/learning | `app/policy/**`, `app/learning/**`, `app/experiments/**`, `app/interventions/**`, reward/verification/observe semantics |
| A5 Reliability + integration | `backend/tests/**` (new `tests/reliability/`, evals under the existing eval harness), API error mapping/health/pagination/perf/logging, README/Progress/Decisions/.env.example/Makefile, regenerate frontend types, final integration, ALL commits |

Schema changes: anyone may edit models they own contractually, but record the request in `docs/notes/schema-requests.md`; **only A1 (and A5 at the end) regenerate the migration** (`scripts/regen_initial_migration.sh` / alembic), then `alembic check`, fresh-DB upgrade (`scripts/fresh_db_smoke.sh`). No manual DB patching. Never alter the fixture experiment row.

## Cross-agent contracts
1. Model runtime: domain code (A2, A4) calls only `ModelGateway` (`generate_structured`, `generate_text`, `health`). No `openai.OpenAI(...)`/`anthropic.Anthropic(...)` outside adapters/tests.
2. Domain state: A2–A4 use A1's domain services/state machines; no raw SQL state transitions or direct status assignment.
3. Policy: A4 owns action choice. A2 never chooses action; A3's model never chooses action type (it drafts details for the policy-selected action).
4. Evidence: A2 creates evidence; models may classify/summarize but not invent evidence or URLs.
5. Conflicts → A5 resolves.

## Provider-neutral model runtime (lock)
Config (generic, active runtime only): `MODEL_PROVIDER`, `MODEL_API_PROTOCOL` (`openai_chat` | `openai_responses` | `anthropic_messages`), `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`. No provider-specific mandatory env chaos (optional aliases allowed). `create_model_gateway(settings)` factory → `OpenAICompatibleAdapter` (OpenAI, NVIDIA NIM, vLLM, others via base URL) or `AnthropicAdapter` (native Messages). No NVIDIA-specific adapter unless proven incompatible. Provider-neutral request object (system, messages/input, temperature, max_output_tokens, response_schema, metadata, timeout, purpose, prompt version). Capabilities flags (structured_output, tool_calling, streaming, json_schema, max_context). Health: NOT_CONFIGURED | READY | AUTH_FAILED | DEGRADED. Metadata persisted: provider, model, protocol, purpose, prompt version, latency, tokens if available, timestamp — never hidden reasoning/chain of thought. Task-specific temperature/max tokens/timeout via config (low temperature for state-affecting calls). Bounded retries (timeout/429/5xx), no infinite retries, one safe repair attempt for malformed JSON then explicit failure. Concurrency semaphore. Caching only for deterministic extraction/classification. Untrusted web content is DATA (prompt-injection tested); evidence passed with immutable IDs, unknown IDs rejected; model-mentioned URLs not retrieved by the fetch subsystem never become evidence. Prompt versions: intent_classifier_v1, hypothesis_generator_v3, counterevidence_assessor_v2, intervention_drafter_v2, incident_summarizer_v1 (adapt names), persisted. Provider unavailable → investigation DEGRADED, everything else keeps working. `make model-smoke` (tiny structured request, redacted). Contract tests: same domain call against fake OpenAI-compatible adapter and fake Anthropic adapter. README documents only adapter-tested providers.

## Targets (A5 verifies; release-blocking if missed)
Unsupported confirmation rate 0; temporal leakage 0; false reward 0 on the deterministic benchmark. 0 unexplained backend failures; ruff clean; `alembic check/current/heads` clean; `make audit-db` clean; model + Profound contract tests pass; frontend typecheck/Vitest/build pass (regenerate types if OpenAPI changed); fixture Experiment 1 still awaiting_verification, after_metrics empty, reward empty, eligible_at 2026-10-04T20:29:43Z; API restarted onto final commit (health reports git SHA if supported).

## Docs (A5, only after verification)
Progress.md, Decisions.md (add: provider-neutral ModelGateway decision; GitHub optional decision if not already clear), README model section, `.env.example`. Don't churn THIRD_PARTY.md unless code was copied.
