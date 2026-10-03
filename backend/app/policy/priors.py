"""Cold-start priors: explicit, human-readable, hand-set configuration. NOT learned.

Each rule says: "when the incident context satisfies ALL conditions, these actions get this prior
expected-reward score". Scores are on the same scale as the reward (`compute_reward`, roughly
[-1, 1]; useful interventions are ~0.1-0.5). Multiple matching rules combine by per-action MAX.
If no rule matches, `DEFAULT_PRIOR` applies (conservative: slight preference for `observe`).

Conditions reference feature names from `app.policy.features.FEATURE_NAMES` with a comparison op.
They are plain data so the exact priors in force are stored on every `PolicyVersion.priors`.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass
from typing import Any

import numpy as np

from app.domain.enums import ActionType
from app.policy.features import FEATURE_NAMES

A = ActionType
_OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt}
_INDEX = {n: i for i, n in enumerate(FEATURE_NAMES)}

Condition = tuple[str, str, float]


@dataclass(frozen=True)
class PriorRule:
    name: str
    description: str
    when: tuple[Condition, ...]
    scores: dict[ActionType, float]

    def matches(self, x: np.ndarray) -> bool:
        return all(_OPS[op](x[_INDEX[f]], v) for f, op, v in self.when)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description,
                "when": [list(c) for c in self.when], "scores": {a.value: s for a, s in self.scores.items()}}


COLD_START_PRIORS: tuple[PriorRule, ...] = (
    PriorRule(
        "owned_outdated_page",
        "Our own page is the cited source, it exists, and it is stale -> refresh that page.",
        (("owned_source", ">=", 0.5), ("content_exists", ">", 0.5), ("source_freshness", "<", 0.4)),
        {A.UPDATE_EXISTING_PAGE: 0.50, A.STRUCTURED_DATA: 0.20, A.CREATE_FAQ: 0.20, A.OBSERVE: 0.05},
    ),
    PriorRule(
        "owned_factual_conflict",
        "A confirmed-looking factual conflict on a page we own -> correct the page.",
        (("owned_source", ">=", 0.5), ("content_exists", ">", 0.5), ("factual_conflict", ">=", 0.5)),
        {A.UPDATE_EXISTING_PAGE: 0.50, A.CREATE_FAQ: 0.20, A.STRUCTURED_DATA: 0.15},
    ),
    PriorRule(
        "third_party_misinformation",
        "A third-party source states something conflicting -> ask the publisher to correct it.",
        (("third_party_source", ">=", 0.5), ("factual_conflict", ">=", 0.5)),
        {A.PUBLISHER_OUTREACH: 0.50, A.CREATE_CANONICAL_PAGE: 0.25, A.CREATE_FAQ: 0.20, A.OBSERVE: 0.05},
    ),
    PriorRule(
        "missing_canonical_info",
        "We have no page covering the topic that models are asked about -> publish a canonical page.",
        (("content_exists", "<", 0.5), ("prompt_volume", ">=", 0.4)),
        {A.CREATE_CANONICAL_PAGE: 0.50, A.CREATE_FAQ: 0.30, A.STRUCTURED_DATA: 0.10},
    ),
    PriorRule(
        "competitor_displacement",
        "A competitor is gaining presence on prompts we used to win -> comparison content.",
        (("competitor_delta", ">=", 0.2), ("visibility_delta", "<=", -0.1)),
        {A.CREATE_COMPARISON_CONTENT: 0.45, A.UPDATE_EXISTING_PAGE: 0.25, A.CREATE_FAQ: 0.20},
    ),
    PriorRule(
        "lost_citation_owned_content_ok",
        "Citations fell although fresh owned content exists -> make it easier to extract (structured data).",
        (("citation_delta", "<=", -0.15), ("content_exists", ">", 0.5), ("owned_source", ">=", 0.5),
         ("source_freshness", ">", 0.5)),
        {A.STRUCTURED_DATA: 0.40, A.CREATE_FAQ: 0.30, A.UPDATE_EXISTING_PAGE: 0.20},
    ),
    PriorRule(
        "minor_low_volume_fluctuation",
        "Small move on low-volume prompts with no conflict -> likely noise; watch, do not change anything.",
        (("prompt_volume", "<=", 0.4), ("visibility_delta", ">=", -0.15), ("factual_conflict", "<", 0.5)),
        {A.OBSERVE: 0.50},
    ),
)

# Applies when no rule matches: no evidence for any intervention, so prefer to watch.
DEFAULT_PRIOR: dict[ActionType, float] = {a: 0.10 for a in ActionType} | {A.OBSERVE: 0.20}


def prior_scores(x: np.ndarray, rules: tuple[PriorRule, ...] = COLD_START_PRIORS) -> tuple[dict[ActionType, float], list[str]]:
    """Per-action prior expected reward for context x, plus the names of the rules that matched."""
    matched = [r for r in rules if r.matches(x)]
    if not matched:
        return dict(DEFAULT_PRIOR), ["default"]
    out = {a: DEFAULT_PRIOR[a] * 0.5 for a in ActionType}  # unlisted actions: below default, still legal
    for r in matched:
        for a, s in r.scores.items():
            out[a] = max(out[a], s)
    return out, [r.name for r in matched]


def priors_to_json(rules: tuple[PriorRule, ...] = COLD_START_PRIORS) -> dict[str, Any]:
    return {"rules": [r.to_json() for r in rules], "default": {a.value: s for a, s in DEFAULT_PRIOR.items()},
            "combination": "per-action max over matching rules; unlisted actions get 0.5*default"}
