# SLAP reference notes (A10)

- Repo: https://github.com/mogonlac/hackupc2026 (README title "SLAP", HackUPC 2026, Bending Spoons challenge winner). Found via `gh api search/repositories`; `gh search repos SLAP hackupc` only returns the unrelated `Haradai/slapslap_HackUPC2024`. Cloned to `references/slap`.
- SHA: `89eb5b3f66d9fda07ed5ef16c750e220b05ec5d7` (2026-04-29, "removed extra line - README.md")
- License: **no LICENSE file**. README "License" section says only "HackUPC 2026 License" (undefined); `backend/package.json` declares `"license": "ISC"` but no ISC text/copyright is shipped and the repo-level statement conflicts. Under the license gate this is ambiguous -> **ideas only, zero code copied.**

## What was studied
- `backend/src/github.js`: Gemini reads a file and returns JSON `{file, content, summary}`; Octokit builds blob/tree/commit, creates `slap/<ts>` branch, opens a PR; also has `mergePR` (squash) and `closePR`.
- README lifecycle: Slack message -> LLM structuring -> pending GitHub issue -> developer approval; "human approval" as the safety valve.

## Ideas adapted (reimplemented, nothing copied)
- Structured JSON output from the LLM (file, content, summary) validated before use -> our `PatchDraft` pydantic schema.
- Branch -> commit -> PR as the auditable handoff to engineering.

## Deliberate differences
- We NEVER merge (SLAP exposes `mergePR`); executor has no merge call and tests assert none is issued.
- LLM may not pick files: the deterministic template fixes the allowed paths; draft claims must cite supplied facts; numbers/URLs/acronyms must occur in evidence.
- Branch names are deterministic (`aeo-sre/<incident>-<slug>`) for idempotent retries; SLAP uses a timestamp.
- Uses GitHub REST contents API via httpx (no Octokit/Git-data trees); dry-run records exact planned calls.
