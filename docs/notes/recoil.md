# Recoil AI reference notes (A10)

- Repo: https://github.com/A-Piyas-04/Recoil-AI, cloned to `references/recoil-ai`.
- SHA: `a65f14fa2a3b6e8a9d687f31d6008678772c16b3` (2026-06-03)
- License: **none** (no LICENSE file; README "License" section reads "Add your license here (e.g. MIT)"). **Ideas only, zero code copied.**

## What was studied
- README + `backend/app/services/ai/prompts.py`: single-request "campaign red-team" (activist, journalist, competitor, meme persona), backfire/backlash risk score with breakdown, brand-consistency check against prior messaging, "pre-mortem" generation. System prompt insists on grounding in the supplied copy and not claiming data it lacks.

## Ideas adapted
- Treat generated content as something to challenge before it ships: our risk level (`assess_risk`) is bumped when the root cause is unconfirmed or the patch has no cited facts, and the PR body carries explicit review notes.
- "Be grounded in the provided copy; do not claim real data you do not have" -> the patch-generation system prompt and the grounding validator (`app/interventions/facts.py`).
- Risk as a decomposed, visible quantity rather than a hidden score (our `Risk` enum plus rationale text).

## Not adopted
- Persona simulation / meme generation / numeric backfire scoring: out of scope; no LLM-invented risk numbers (the brief forbids fabricated metrics).
