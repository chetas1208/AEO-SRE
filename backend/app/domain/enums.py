"""Shared vocabulary. SINGLE SOURCE OF TRUTH — do not redefine elsewhere. Frontend types mirror these."""
from enum import StrEnum


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class IncidentState(StrEnum):
    DETECTED = "detected"
    TRIAGED = "triaged"
    INVESTIGATING = "investigating"
    EVIDENCE_READY = "evidence_ready"
    ROOT_CAUSE_PROPOSED = "root_cause_proposed"
    ROOT_CAUSE_CONFIRMED = "root_cause_confirmed"
    INTERVENTION_PROPOSED = "intervention_proposed"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    AWAITING_VERIFICATION = "awaiting_verification"
    VERIFIED = "verified"
    REWARDED = "rewarded"
    CLOSED = "closed"
    DISMISSED = "dismissed"
    FAILED = "failed"


class IncidentCategory(StrEnum):
    VISIBILITY_DROP = "visibility_drop"
    COMPETITOR_CITATION_GAIN = "competitor_citation_gain"
    FACTUAL_CONFLICT = "factual_conflict"
    LOST_CITATION_SOURCE = "lost_citation_source"
    PROMPT_VOLUME_SPIKE = "prompt_volume_spike"
    NEW_COMPETITOR_CONTENT = "new_competitor_content"
    STALE_INFORMATION = "stale_information"


class ActionType(StrEnum):
    OBSERVE = "observe"
    UPDATE_EXISTING_PAGE = "update_existing_page"
    CREATE_FAQ = "create_faq"
    CREATE_CANONICAL_PAGE = "create_canonical_page"
    CREATE_COMPARISON_CONTENT = "create_comparison_content"
    PUBLISHER_OUTREACH = "publisher_outreach"
    STRUCTURED_DATA = "structured_data"


class ApprovalStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    MODIFIED = "modified"
    EXPIRED = "expired"


class ExperimentStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    EXECUTING = "executing"
    EXECUTED = "executed"
    AWAITING_VERIFICATION = "awaiting_verification"
    VERIFIED = "verified"
    REWARDED = "rewarded"
    REJECTED = "rejected"
    FAILED = "failed"


class EvidenceType(StrEnum):
    PROFOUND = "profound"
    OWNED = "owned"
    COMPETITOR = "competitor"
    EXTERNAL = "external"
    INFERENCE = "inference"


class EvidenceStatus(StrEnum):
    LIVE = "live"
    CHANGED = "changed"
    STALE = "stale"
    UNAVAILABLE = "unavailable"
    FAILED = "failed"


class EdgeType(StrEnum):
    CITES = "cites"
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CHANGED_BEFORE = "changed_before"
    ASSOCIATED_WITH = "associated_with"
    CONTAINS_CLAIM = "contains_claim"
    COMPETES_WITH = "competes_with"
    TRIGGERED = "triggered"


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"      # hypothesis, NOT fact
    CONFIRMED = "confirmed"    # passed evidence gate
    REJECTED = "rejected"


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    WARNING = "warning"
    FAILED = "failed"
    WAITING = "waiting"


class SelectionBasis(StrEnum):
    COLD_START_PRIOR = "cold_start_prior"
    LEARNED_POLICY = "learned_policy"
    RULE_FALLBACK = "rule_fallback"
    MANUAL_OVERRIDE = "manual_override"


class Risk(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class CapabilityState(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
