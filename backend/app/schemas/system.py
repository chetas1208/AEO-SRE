from datetime import datetime
from typing import Any

from app.domain.enums import CapabilityState
from app.schemas.common import ApiModel


class Capability(ApiModel):
    key: str
    label: str
    state: CapabilityState
    detail: str | None = None
    last_success: datetime | None = None
    last_error: str | None = None
    last_error_at: datetime | None = None
    meta: dict[str, Any] = {}


class CapabilitiesOut(ApiModel):
    checked_at: datetime
    overall: CapabilityState
    capabilities: dict[str, Capability]
    executors: dict[str, Capability] = {}  # manual is always healthy; optional executors never degrade `overall`
    last_ingestion: datetime | None = None
    last_investigation: datetime | None = None
    last_policy_update: datetime | None = None
    model_artifact_version: str | None = None
    next_ingestion: datetime | None = None
    graph: dict[str, Any] | None = None  # Neo4j projection state + detected capabilities (never affects `overall`)


class HealthOut(ApiModel):
    status: str
    version: str
    time: datetime
    database: CapabilityState
    redis: CapabilityState
    profound: str = "not_configured"  # configured | not_configured; not a live probe
    model_provider: str = "not_configured"
    profound_state: str = "NOT_CONFIGURED"  # NOT_CONFIGURED | UNVERIFIED | READY | DEGRADED (from the last ingest; no probe)
    model_state: str = "NOT_CONFIGURED"  # NOT_CONFIGURED | READY | AUTH_FAILED | DEGRADED (from recent calls; no probe)
    git_sha: str | None = None
    neo4j_state: str = "NOT_CONFIGURED"  # NOT_CONFIGURED | READY | DEGRADED | AUTH_FAILED; never degrades `status`
