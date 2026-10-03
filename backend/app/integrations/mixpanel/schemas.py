"""Canonical shapes for Mixpanel ingestion (provider-neutral at the boundary)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

CorrelationConfidence = Literal["HIGH", "MEDIUM", "LOW"]
CorrelationMethod = Literal["EXACT_ID", "SESSION", "TEMPORAL", "HEURISTIC", "UNRESOLVED"]
TenantResolution = Literal["RESOLVED", "UNRESOLVED"]


class NormalizedLiveEvent(BaseModel):
    event_id: str
    source: Literal["MIXPANEL"] = "MIXPANEL"
    source_event: str
    source_event_id: str
    occurred_at: datetime
    received_at: datetime
    organization_id: str | None = None
    tenant_resolution: TenantResolution = "UNRESOLVED"
    campaign_id: str | None = None
    agent_id: str | None = None
    experiment_id: str | None = None
    product_id: str | None = None
    properties: dict[str, Any] = Field(default_factory=dict)
    correlation_keys: dict[str, str] = Field(default_factory=dict)
    raw_hash: str = ""
    correlation_method: CorrelationMethod = "UNRESOLVED"
    correlation_confidence: CorrelationConfidence = "LOW"


class IngestBatchResult(BaseModel):
    mode: Literal["BACKFILL", "LIVE"]
    window_from: datetime
    window_to: datetime
    fetched: int = 0
    inserted: int = 0
    duplicates: int = 0
    unresolved_tenant: int = 0
    correlated: int = 0
    latency_ms: float = 0.0
    status: str = "ok"
