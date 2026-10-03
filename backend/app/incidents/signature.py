"""Incident signature: the stable description of WHAT regressed, used for dedup, history and policy context.

signature = {incident_type, family, topic, cluster_id, platform, persona, competitor, competitors, primary_metric,
direction, signals, signature_key}. `platform` / `persona` are set only when EVERY primary anomaly was observed in that
one segment: a ChatGPT-only or CISO-only regression stays labeled as such and is never generalized to the cluster.
B1's fingerprint (app.incidents.fingerprint) reads `incident_type/topic/persona/platform/competitors` from here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from typing import Any

# metric key -> signal family (what kind of evidence of regression it is)
SIGNAL_FAMILY: dict[str, str] = {
    "visibility": "visibility_regression", "avg_position": "visibility_regression",
    "citation_share": "citation_loss", "cited_sources": "citation_loss", "lost_sources": "citation_loss",
    "competitor_share": "competitor_gain", "new_competitor_content": "competitor_gain",
    "accuracy": "accuracy_drop", "factual_conflicts": "accuracy_drop", "stale_sources": "stale_sources",
    "prompt_volume": "demand_change",
}
FANOUT_SIGNAL = "query_fanout_change"


def segment_of(source: str | None) -> tuple[str | None, str | None]:
    """('profound:model=ChatGPT') -> ('model', 'ChatGPT'); headline series -> (None, None)."""
    s = (source or "")
    if ":" not in s:
        return None, None
    _, _, seg = s.partition(":")
    dim, _, val = seg.partition("=")
    return (dim or None, val or None) if val else (None, None)


def scope_of(source: str | None) -> tuple[str | None, str | None]:
    """(platform, persona) a series is scoped to. Topic/region/other segments are not a platform/persona scope."""
    dim, val = segment_of(source)
    if dim == "model":
        return val, None
    if dim == "persona":
        return None, val
    return None, None


def common_scope(sources: Iterable[str | None]) -> tuple[str | None, str | None]:
    """Platform/persona shared by ALL series; any unscoped or differing series -> None (not generalized upward)."""
    scopes = [scope_of(s) for s in sources]
    if not scopes:
        return None, None
    platform = scopes[0][0] if all(sc[0] == scopes[0][0] for sc in scopes) else None
    persona = scopes[0][1] if all(sc[1] == scopes[0][1] for sc in scopes) else None
    return platform, persona


def build_signature(
    *, incident_type: str, family: str, topic: str, cluster_id: str | None, platform: str | None, persona: str | None,
    competitors: list[str], primary_metric: str, direction: str, metrics: list[str], signal_families: Iterable[str],
) -> dict[str, Any]:
    families = sorted(set(signal_families))
    sig: dict[str, Any] = {
        "incident_type": incident_type, "family": family, "topic": topic, "cluster_id": cluster_id,
        "platform": platform, "persona": persona,
        "competitor": competitors[0] if competitors else None, "competitors": competitors,
        "primary_metric": primary_metric, "direction": direction, "metrics": metrics, "signals": families,
    }
    sig["signature_key"] = signature_key(sig)
    return sig


def signature_key(sig: dict[str, Any]) -> str:
    """Dedup/history key: family + cluster (or topic) + platform + persona. Competitor and metric are descriptive
    (a second competitor joining the same displacement is the same incident)."""
    parts = [sig.get("family") or sig.get("incident_type"), sig.get("cluster_id") or sig.get("topic"),
             (sig.get("platform") or "").lower(), (sig.get("persona") or "").lower()]
    return hashlib.sha256(json.dumps(parts).encode()).hexdigest()[:24]
