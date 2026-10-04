from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from app.schemas.common import ApiModel


class DiscoveryGapRecommendedAction(ApiModel):
    type: Literal[
        "update_canonical_page",
        "correct_source",
        "create_faq",
        "clarify_pricing",
        "add_structured_evidence",
        "observe",
    ]
    title: str
    description: str


class DiscoveryGapProductTruth(ApiModel):
    statement: str
    canonical_key: str | None = None
    canonical_source: str | None = None
    verified_at: str | None = None


class DiscoveryGapAiPerception(ApiModel):
    claim: str
    engines: list[str] = []
    likely_source: str | None = None
    citations_count: int = 0


class DiscoveryGapProfoundMetrics(ApiModel):
    visibility_pct: float = 0.0
    citation_share_pct: float = 0.0
    prompt_coverage_pct: float = 0.0
    competitor_share_pct: float = 0.0
    top_sources: list[str] = []


class DiscoveryGapItemOut(ApiModel):
    id: str
    incident_id: uuid.UUID
    number: int
    intent_class: str
    product_name: str
    actual_fit_pct: int
    ai_perceived_fit_pct: int
    gap_pp: int
    gap_type: Literal[
        "MISSING_CAPABILITY",
        "INCORRECT_CAPABILITY",
        "STALE_INFORMATION",
        "MISSING_CITATION",
        "WRONG_TIER_PRICING",
        "UNKNOWN",
    ]
    prompt_clusters_count: int
    confidence: float
    severity: Literal["critical", "high", "medium", "low"]
    detected_at: datetime
    source_mode: Literal["LIVE", "SIMULATED", "TEST"]
    product_truth: DiscoveryGapProductTruth
    ai_perception: DiscoveryGapAiPerception
    profound_metrics: DiscoveryGapProfoundMetrics
    recommended_action: DiscoveryGapRecommendedAction
    approval_status: Literal["pending", "approved", "rejected", "modified"] = "pending"


class DiscoveryGapListOut(ApiModel):
    items: list[DiscoveryGapItemOut]
    total: int
    source: Literal["live", "fixture"] = "live"
