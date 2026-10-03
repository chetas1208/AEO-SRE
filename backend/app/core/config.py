"""Settings. Single root .env at repo root (../.env relative to backend/)."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    @property
    def neo4j_active(self) -> bool:
        """True when the graph projection should be used: explicit flag, else URI + password present."""
        if self.neo4j_enabled is not None:
            return self.neo4j_enabled and bool(self.neo4j_uri)
        return bool(self.neo4j_uri and self.neo4j_password)

    @property
    def model_protocol(self) -> str:
        return normalize_model_protocol(self.model_api_protocol)

    @property
    def model_resolved_base_url(self) -> str:
        return (self.model_base_url or MODEL_DEFAULT_BASE_URLS.get(self.model_protocol, "")).rstrip("/")

    @property
    def model_configured(self) -> bool:
        return bool(self.model_api_key) or (self.model_allow_no_key and bool(self.model_base_url))

    @property
    def resolved_fast_model(self) -> str:
        return self.model_fast_name or self.model_name or "claude-haiku-4-5"

    @property
    def resolved_deep_model(self) -> str:
        if self.model_deep_name:
            return self.model_deep_name
        if self.model_protocol == "anthropic_messages" and "claude" in (self.model_name or "").lower():
            return "claude-sonnet-4-6"
        return ""

    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql+psycopg://aeo:aeo@localhost:5432/aeo"
    redis_url: str = "redis://localhost:6379/0"
    # Provider-neutral model runtime. Active runtime only; see docs/notes/handoff-b3.md.
    model_provider: str = ""  # free-form label (openai, nvidia_nim, vllm, anthropic, ...): metadata only
    model_api_protocol: str = "anthropic_messages"  # openai_chat | openai_responses | anthropic_messages
    model_base_url: str = ""  # empty -> official default for the protocol (see MODEL_DEFAULT_BASE_URLS)
    model_api_key: str = ""
    model_name: str = "claude-haiku-4-5"
    model_fast_name: str = ""  # empty -> defaults to resolved_fast_model (claude-haiku-4-5)
    model_deep_name: str = ""  # empty -> defaults to resolved_deep_model (claude-sonnet-4-6)
    model_default_tier: str = "fast"  # "fast" | "deep"
    model_escalation_enabled: bool = True
    model_max_deep_calls_per_investigation: int = 1
    model_max_calls_per_investigation: int = 3
    model_complexity_threshold: float = 0.65
    model_allow_no_key: bool = False  # self-hosted OpenAI-compatible servers (vLLM) that need no key
    model_max_concurrency: int = 4
    model_max_retries: int = 2
    model_cache_enabled: bool = True  # only deterministic extraction/classification calls are ever cached
    model_task_overrides: str = ""  # JSON: {"HYPOTHESIS_GENERATION": {"temperature": 0.1, "timeout_s": 90}}
    profound_api_key: str = ""
    profound_base_url: str = ""
    github_token: str = ""
    github_owner: str = ""
    github_repo: str = ""
    github_base_branch: str = "main"
    github_content_root: str = ""
    github_content_extension: str = ".md"
    # Change Guard: bearer token for POST /api/change-checks. Unset -> the endpoint answers 503 (secure by default).
    change_guard_token: str = ""
    change_guard_rate_limit_per_minute: int = 120
    change_guard_pending_ttl_hours: float = 72.0  # how long an un-actioned external ChangeSet counts as "pending"
    # Muse connector (docs/MUSE_CONNECTOR.md). Key unset -> every /muse/tools/* call answers 503 (secure by default).
    muse_connector_api_key: str = ""
    muse_organization_id: str = ""  # the ONE organization the connector key may read (uuid) ...
    muse_organization_domain: str = ""  # ... or its domain; no cross-org access
    muse_rate_limit_per_minute: int = 60
    muse_allow_simulated: bool = False  # honour X-Muse-Source-Mode: SIMULATED (tests / demos); ignored when false
    muse_request_timeout_s: float = 15.0
    # Muse Spark (provider 'meta'): tokens added to every max_output_tokens because reasoning tokens count against it.
    model_reasoning_headroom: int = 4096
    # Neo4j graph projection (rebuildable; Postgres stays the system of record). See docs/GRAPH_SPEC.md.
    # neo4j_enabled=None -> auto: enabled when URI and password are both present. Explicit false always wins.
    neo4j_enabled: bool | None = None
    neo4j_uri: str = ""
    neo4j_username: str = "neo4j"
    neo4j_password: str = ""
    neo4j_database: str = ""  # empty -> server default database
    neo4j_gds_enabled: bool = False  # GDS algorithms are used only when this is true AND the plugin is detected
    neo4j_aura_api_client_id: str = ""  # Aura API (Graph Analytics detection); unset -> reported not_configured
    neo4j_aura_api_client_secret: str = ""
    neo4j_connect_timeout_s: float = 5.0
    neo4j_acquisition_timeout_s: float = 5.0
    neo4j_query_timeout_s: float = 10.0
    neo4j_max_pool_size: int = 20
    neo4j_max_retry_time_s: float = 5.0  # managed-transaction retry budget for transient errors only
    neo4j_health_cache_s: float = 15.0
    # Graph projection (outbox -> Neo4j). See app/graph/outbox.py and docs/notes/handoff-n2.md.
    graph_outbox_enabled: bool = True  # False: domain transactions stop writing outbox rows (replay can rebuild them)
    graph_stale_seconds: int = 300  # oldest unprocessed outbox row older than this => graph context is stale
    graph_outbox_batch: int = 50
    graph_outbox_max_attempts: int = 8  # poison rows are dead-lettered after this many failed (non-outage) attempts
    graph_outbox_backoff_base_s: float = 5.0
    graph_outbox_backoff_max_s: float = 900.0
    app_base_url: str = "http://localhost:3000"
    cors_allowed_origins: str | None = None
    backend_base_url: str = "http://localhost:8000"
    # Profound data lags 24-48h: hours to wait after a real execution before post-intervention metrics count.
    verification_delay_hours: float = 48.0
    auto_investigate: bool = True
    policy_seed: int | None = None  # tests/dev only: makes the bandit's action sampling reproducible
    # Investigation stops and reports insufficient rather than looping. Defaults match the previous collector cap.
    max_web_sources: int = 24
    max_hypotheses: int = 5
    max_model_calls: int = 2
    max_investigation_seconds: float = 180.0
    max_answer_rows: int = 20
    # Control policy (Change Guard shadow / GraphLinUCB). See docs/AGENTMATCH_DECISION_LAYER.md.
    control_policy_mode: str = "SHADOW"  # SHADOW | ACTIVE | BASELINE_ONLY
    control_policy_shadow_enabled: bool = True
    control_policy_linucb_alpha: float = 0.5
    # Laya System-1 prior (optional local checkpoint).
    laya_enabled: bool = False
    laya_checkpoint: str = "convaiinnovations/laya-typed-decisions"
    laya_confidence_accept: float = 0.72
    laya_confidence_review: float = 0.55
    laya_confidence_escalate: float = 0.40


# Backward-compatible protocol aliases (older .env files used anthropic | openai).
MODEL_PROTOCOL_ALIASES = {
    "anthropic": "anthropic_messages", "anthropic_messages": "anthropic_messages",
    "openai": "openai_chat", "openai_chat": "openai_chat", "openai_responses": "openai_responses",
}
MODEL_DEFAULT_BASE_URLS = {
    "anthropic_messages": "https://api.anthropic.com",
    "openai_chat": "https://api.openai.com/v1",
    "openai_responses": "https://api.openai.com/v1",
}


def normalize_model_protocol(value: str) -> str:
    """Canonical protocol name, or the lower-cased input when unknown (the factory then raises)."""
    v = (value or "").strip().lower()
    return MODEL_PROTOCOL_ALIASES.get(v, v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
