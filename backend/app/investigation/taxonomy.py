"""Constrained root-cause taxonomy. Every hypothesis maps to exactly one cause (or INSUFFICIENT_EVIDENCE).

Existing RCA rule ids / layers (app.investigation.rca) are adapted onto this enum; the enum is the vocabulary the gate,
confidence model, graph and UI-facing context use. The model never invents a new cause.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class RootCause(StrEnum):
    COMPETITOR_CANONICAL_IMPROVED = "competitor_canonical_content_improved"
    OWNED_STALE = "owned_content_stale"
    OWNED_BURIED = "owned_content_buried"
    OWNED_MISSING = "owned_content_missing"
    THIRD_PARTY_MISINFORMATION = "third_party_misinformation"
    CITATION_SOURCE_SHIFT = "citation_source_shift"
    QUERY_INTERPRETATION_SHIFT = "query_interpretation_shift"
    CANONICAL_FACT_CHANGED = "canonical_fact_changed"
    MODEL_VARIANCE = "model_variance"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


# Causes that blame the brand's own situation or a named external party. A category-wide movement (every control moved)
# must be ruled out before any of these is confirmed.
BRAND_SPECIFIC: frozenset[RootCause] = frozenset(RootCause) - {RootCause.MODEL_VARIANCE, RootCause.INSUFFICIENT_EVIDENCE}

RULE_TO_CAUSE: dict[str, RootCause] = {
    "competitor_canonical_improved": RootCause.COMPETITOR_CANONICAL_IMPROVED,
    "owned_content_stale": RootCause.OWNED_STALE,
    "capability_exists_but_buried": RootCause.OWNED_BURIED,
    "owned_content_missing": RootCause.OWNED_MISSING,
    "obsolete_third_party_info": RootCause.THIRD_PARTY_MISINFORMATION,
    "canonical_truth_conflicts_with_web": RootCause.THIRD_PARTY_MISINFORMATION,
    "citation_source_changed": RootCause.CITATION_SOURCE_SHIFT,
    "query_interpretation_shifted": RootCause.QUERY_INTERPRETATION_SHIFT,
    "canonical_fact_changed": RootCause.CANONICAL_FACT_CHANGED,
    "model_variance": RootCause.MODEL_VARIANCE,
    "no_actionable_cause": RootCause.INSUFFICIENT_EVIDENCE,
}
LAYER_TO_CAUSE: dict[str, RootCause] = {
    "competitor": RootCause.COMPETITOR_CANONICAL_IMPROVED,
    "owned_content": RootCause.OWNED_STALE,
    "external_web": RootCause.THIRD_PARTY_MISINFORMATION,
    "citation": RootCause.CITATION_SOURCE_SHIFT,
    "ai_engine": RootCause.QUERY_INTERPRETATION_SHIFT,
    "canonical_truth": RootCause.CANONICAL_FACT_CHANGED,
    "undetermined": RootCause.INSUFFICIENT_EVIDENCE,
}


def cause_for(rule_id: str | None, layer: str | None = None, title: str = "") -> RootCause:
    """Map a hypothesis to the taxonomy. `prompt_cluster_changed` is model variance only when its title says the
    engine itself changed; otherwise it is an interpretation shift."""
    if rule_id == "prompt_cluster_changed":
        return RootCause.MODEL_VARIANCE if "model update" in title.lower() else RootCause.QUERY_INTERPRETATION_SHIFT
    if rule_id in RULE_TO_CAUSE:
        return RULE_TO_CAUSE[rule_id]
    if layer in LAYER_TO_CAUSE:  # llm hypotheses carry a layer, not a rule id
        return LAYER_TO_CAUSE[layer]
    return RootCause.INSUFFICIENT_EVIDENCE


def cause_of_meta(meta: dict[str, Any] | None, title: str = "") -> RootCause:
    meta = meta or {}
    return cause_for(meta.get("rule_id"), meta.get("layer"), title)
