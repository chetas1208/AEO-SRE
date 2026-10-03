"""Deterministic incident fingerprint (dedup key). Pure; no DB access.

fingerprint = sha256 over: org, incident type, topic / prompt cluster, persona, platform/model, affected
competitor/source set, and a time bucket (UTC day of first observation). The detector's (family, cluster) filter stays
the primary dedup; this key is the persisted, uniquely-indexed backstop (one OPEN incident per fingerprint).
Optional context hints the detector may supply in ``context["signature"]``: ``persona``, ``platform``/``engine``,
``competitors``, ``sources``.
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

BUCKET_FORMAT = "%Y-%m-%d"


def _norm(v: Any) -> str:
    return str(v).strip().lower() if v not in (None, "") else ""


def _as_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return sorted({_norm(x) for x in v if _norm(x)})
    return [_norm(v)]


def time_bucket(at: datetime | None) -> str:
    if at is None:
        return ""
    at = at if at.tzinfo else at.replace(tzinfo=UTC)
    return at.astimezone(UTC).strftime(BUCKET_FORMAT)


def fingerprint_parts(
    *, org_id: Any, incident_type: Any, topic: Any = None, prompt_cluster_id: Any = None, persona: Any = None,
    platform: Any = None, competitors: Any = None, sources: Any = None, at: datetime | None = None,
) -> list[Any]:
    return [
        _norm(org_id), _norm(getattr(incident_type, "value", incident_type)),
        _norm(prompt_cluster_id) or _norm(topic), _norm(persona), _as_list(platform),
        _as_list(competitors), _as_list(sources), time_bucket(at),
    ]


def fingerprint_from_parts(**kw: Any) -> str:
    return hashlib.sha256(json.dumps(fingerprint_parts(**kw)).encode()).hexdigest()


def incident_fingerprint(inc: Any) -> str:
    ctx = getattr(inc, "context", None) or {}
    sig = ctx.get("signature") or {}
    return fingerprint_from_parts(
        org_id=inc.org_id,
        incident_type=sig.get("incident_type") or inc.category,
        topic=sig.get("topic") or ctx.get("topic"),
        prompt_cluster_id=inc.prompt_cluster_id,
        persona=sig.get("persona") or ctx.get("persona"),
        platform=sig.get("platform") or sig.get("engine") or ctx.get("platform"),
        competitors=sig.get("competitors"),
        sources=sig.get("sources"),
        at=inc.first_observed_at or inc.detected_at,
    )
