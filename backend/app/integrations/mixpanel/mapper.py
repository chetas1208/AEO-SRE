"""Map raw Mixpanel export rows to NormalizedLiveEvent (PII-safe)."""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from app.integrations.mixpanel.schemas import NormalizedLiveEvent

_PII_KEYS = re.compile(
    r"(email|name|ip|phone|address|first_name|last_name|user_email|device_id|session_id)$",
    re.I,
)
_CORRELATION_PROP_KEYS = (
    "campaign_id",
    "campaignId",
    "agent_id",
    "agent_run_id",
    "experiment_id",
    "product_id",
    "correlation_id",
    "request_id",
    "utm_campaign",
    "utm_source",
)


def _redact_properties(props: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in props.items():
        if k.startswith("$") and k not in ("$insert_id", "$device_id", "$user_id"):
            continue
        if _PII_KEYS.search(k):
            out[k] = "[redacted]"
            continue
        if isinstance(v, str) and len(v) > 256:
            out[k] = v[:256] + "…"
            continue
        out[k] = v
    return out


def _parse_time(props: dict[str, Any]) -> datetime:
    raw = props.get("time") or props.get("$time")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(float(raw), tz=UTC)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def stable_source_event_id(event_name: str, props: dict[str, Any]) -> str:
    insert = props.get("$insert_id") or props.get("insert_id")
    if insert:
        return f"insert:{insert}"
    distinct = props.get("distinct_id") or props.get("$distinct_id") or ""
    t = props.get("time") or props.get("$time") or ""
    basis = f"{event_name}|{distinct}|{t}|{props.get('mp_lib', '')}"
    return "hash:" + hashlib.sha256(basis.encode()).hexdigest()[:32]


def extract_correlation_keys(props: dict[str, Any]) -> dict[str, str]:
    keys: dict[str, str] = {}
    for k in _CORRELATION_PROP_KEYS:
        v = props.get(k)
        if v is not None and str(v).strip():
            keys[k] = str(v).strip()
    return keys


def normalize_row(raw: dict[str, Any], *, org_id: UUID | None, received_at: datetime | None = None) -> NormalizedLiveEvent:
    event_name = str(raw.get("event") or raw.get("name") or "unknown")
    props = dict(raw.get("properties") or raw)
    occurred_at = _parse_time(props)
    source_event_id = stable_source_event_id(event_name, props)
    corr = extract_correlation_keys(props)
    redacted = _redact_properties(props)
    raw_hash = hashlib.sha256(repr(sorted(redacted.items())).encode()).hexdigest()[:32]
    org_str = str(org_id) if org_id else None
    return NormalizedLiveEvent(
        event_id=source_event_id,
        source_event=event_name,
        source_event_id=source_event_id,
        occurred_at=occurred_at,
        received_at=received_at or datetime.now(UTC),
        organization_id=org_str,
        tenant_resolution="RESOLVED" if org_id else "UNRESOLVED",
        campaign_id=corr.get("campaign_id") or corr.get("campaignId") or corr.get("utm_campaign"),
        agent_id=corr.get("agent_id") or corr.get("agent_run_id"),
        experiment_id=corr.get("experiment_id"),
        product_id=corr.get("product_id"),
        properties=redacted,
        correlation_keys=corr,
        raw_hash=raw_hash,
    )
