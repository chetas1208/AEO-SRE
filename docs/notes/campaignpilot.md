# CampaignPilot study notes (A3)

Repo: https://github.com/EZZEASY/CampaignPilot (URL from the brief worked; no search needed)
Cloned: `references/campaignpilot`, depth 50, HEAD `0502103dd815e6c65a666176e6eab89915828f57`
License: MIT (Copyright 2026 EZZEASY), clear and permissive. No code was copied; see `third-party-a3.md`.

## What it is
Marketing intelligence agent over Elasticsearch: 50 synthetic campaigns, 11 indices, 14 ES|QL "tools", an anomaly-scan
workflow, a campaign-health workflow, an LLM tool-calling loop (OpenRouter via OpenAI SDK, or Kibana Agent Builder),
and an action-log index. Domain is paid-ads (CPA, CTR, budget), not AI discovery.

## Anomaly detection (`workflows/anomaly_scan.py`, `tools/metrics_tools.py`)
- One function per detector (CPA spike, CTR drop, budget pace, creative fatigue, audience churn, competitor share,
  website health, product price change, support surge), each returning rows over a fixed threshold
  (e.g. CPA +50%, CTR -30%, pace > 1.2, fatigue > 0.7, churn > 0.4).
- Severity is a hardcoded two-step bucket per alert type (`high if x > t2 else medium`), then alerts are sorted by
  severity and grouped. No rolling baseline, no noise model, no dedup, no aggregation across related alerts.
- Takeaways adopted (as ideas): a registry of independent detectors producing a uniform alert shape; unified list
  sorted by severity; per-type thresholds as config. Where we differ: rolling median/MAD baseline + z-gate,
  grouping by prompt cluster, merged displacement family, confidence score, dedup against open incidents,
  continuous 0..1 severity feeding a transparent priority score.

## Tool registry (`agent/tools.py`, `tools/__init__.py`)
- `TOOL_DEFINITIONS` (OpenAI function-calling JSON schemas) + `dispatch_tool(name, args)` mapping name -> Python
  function. Read-only analytics tools plus one write tool (`log_action`). The registry is the fixed vocabulary the
  LLM can act through; it cannot call anything else.
- Takeaway: the same principle applies to our fixed `ActionType` set and to the LLM RCA path (LLM only sees
  evidence we pass and may only cite supplied ids). We did not need a tool-calling loop for RCA: single
  structured-JSON call over pre-collected evidence is safer and testable.

## Cross-source root-cause workflow (system prompt + `check_*` tools)
- Diagnostic protocol in `agent/prompts.py`: surface anomaly -> drill down -> check cross-system signals in a fixed
  order (website -> product -> support) -> classify the cause by LEVEL (ad / website / product / customer /
  competitor) -> recommend a concrete action. The "cross-system" layering (L1 ad, L2 website, L3 product,
  L4 customer) is the key idea.
- Adapted to AEO as layers: AI Engine, Citation, Owned Content, Competitor, External Web, Canonical Truth, each with
  deterministic evidence-pattern rules (`investigation/rca.py`). CampaignPilot leaves the diagnosis to the LLM;
  we run rules first and let an LLM only add hypotheses over supplied evidence, validated by pydantic.
- Also noted: "always query data first, never guess" rule; we enforce it structurally (no evidence, no hypothesis;
  unavailable evidence is never interpreted; a "no actionable cause" fallback exists).

## Action logging (`tools/action_tools.py`)
- `log_action` writes a `proposed` action doc (id, campaign, type, status, timestamp, description, parameters,
  empty outcome) to an `action-log` index; `get_actions` reads it back. Status field is the lifecycle, `outcome`
  is blank until filled in by hand. Nothing computes outcomes.
- Takeaway: logging proposals with parameters and a status is the seed of our Experiment ledger. Ours adds
  approvals, policy version/probability, before/after metrics and measured reward (owned by other agents).

## Not useful / avoided
Streamlit UI, ES|QL mappings, synthetic data generators (violates our no-fake-data rule), Kibana agent setup.
