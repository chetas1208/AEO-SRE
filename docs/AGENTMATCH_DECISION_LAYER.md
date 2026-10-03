# AgentMatch decision layer — capability map & phased plan

Architecture target:

```text
Deterministic rules → Laya (System-1 prior) → Graph context → Contextual bandit → Human gate → Outcome → Reward
```

Expensive LLM only when rules + Laya cannot answer confidently.

## Current → missing → change

| Capability | Existing | Missing | This campaign |
|---|---|---|---|
| Change Guard baseline | `changeguard.service`, `decision.decide` | — | Stays authoritative in ACTIVE mode |
| Safety masks | `control_policy/mask.py` | — | Unchanged |
| Graph features | `graph/features.py`, `control_policy/encoder.py` | Live fetch on every check | Shadow recorder accepts optional graph vector |
| Control policy DB | Migration `0007`, immutable rows | Wired to checks | **Shadow recording** on each `ChangeCheck` |
| Intervention bandit | `policy/bandit.py` LinUCB/Thompson | — | Separate from control-plane bandit |
| Control LinUCB | — | Learner | **`control_policy/learner.py`** (GraphLinUCB v0) |
| Laya runtime | — | Local typed decisions | **`intelligence/laya/`** (optional weights; degrades cleanly) |
| LLM escalation gate | Ad hoc in investigation | Central gate | **`intelligence/escalation.py`** |
| Muse auth | Single org API key | Per-user OAuth | **Phase 2** — see `MUSE_CONNECTOR.md` |
| Multi-tenant users | `Organization` only, `X-Actor` | Users, memberships | **Phase 2** — migration planned |
| Public admin view | Homepage hub | `visibility=PUBLIC` API | **Phase 2** |
| Muse → RL training | — | Must not happen | **DEC enforced** — no connector payload in bandit updates |

## Modes

| Mode | Behavior |
|---|---|
| `BASELINE_ONLY` | Return Change Guard decision only |
| `SHADOW` | Persist baseline + Laya prior + bandit recommendation; caller still gets baseline |
| `ACTIVE` | Bandit recommendation returned (requires graduation criteria — not default) |

Config: `CONTROL_POLICY_MODE=SHADOW` (default).

## Laya

- Apache-2.0 [NandhaKishorM/laya](https://github.com/NandhaKishorM/laya); preferred checkpoint `convaiinnovations/laya-typed-decisions` (benchmark before lock).
- Optional install: `LAYA_ENABLED=true` + model path; else `LAYA_UNAVAILABLE`.
- Never executes side effects; never replaces Change Guard in ACTIVE until eval graduation.

## RL learning source (allowed)

Campaign/agent/control outcomes, human approvals/overrides, first-party feedback — **not** raw Muse connector history.

## Commands

```bash
make eval-guard          # Change Guard regression
# Future: make eval-control-policy, make laya-benchmark
```

## Phase 2 (next)

1. User + membership + OAuth tables and `/oauth/*`
2. Muse tools resolve OAuth token → org scope
3. Public projection API with `visibility=PUBLIC`
4. Frontend signup/login + Settings → Connected Apps
5. Neo4j tenant isolation regression suite expansion
