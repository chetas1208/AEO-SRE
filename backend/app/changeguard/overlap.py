"""Overlap between a proposed change and an experiment's protected scope (pure; the DB lookups live in service.py).

target overlap %          share of the experiment's protected pages that the change touches (exact or section/prefix)
prompt-cluster overlap %  share of the experiment's cluster prompts the change addresses:
                            explicit  - the change lists the cluster id (100) or lists prompts (matched verbatim,
                                        case/space-insensitive)
                            inferred  - the cluster topic is named in the change's claims/text/reason (100, basis
                                        `topic_mention`; reported as inferred, never as a measurement)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.changeguard.targets import match_kind

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", str(s)).strip().casefold()


@dataclass
class ClusterInfo:
    id: str | None
    topic: str
    prompts: list[str] = field(default_factory=list)


def cluster_prompts(raw: Any) -> list[str]:
    """PromptCluster.prompts holds strings (older rows) or {id, text, ...} dicts (Profound rows)."""
    out: list[str] = []
    for p in raw or []:
        if isinstance(p, str):
            out.append(p)
        elif isinstance(p, dict) and p.get("text"):
            out.append(str(p["text"]))
    return out


@dataclass
class TargetOverlap:
    pct: float = 0.0
    matches: list[dict[str, str]] = field(default_factory=list)
    protected_total: int = 0


def target_overlap(change_target: str | None, protected: list[str]) -> TargetOverlap:
    if not change_target or not protected:
        return TargetOverlap(0.0, [], len(protected))
    matches = []
    for t in protected:
        kind = match_kind(change_target, t)
        if kind:
            matches.append({"target": t, "match": kind})
    return TargetOverlap(round(100.0 * len(matches) / len(protected), 1), matches, len(protected))


@dataclass
class ClusterOverlap:
    pct: float = 0.0
    basis: str | None = None  # cluster_id | prompts | topic_mention


def cluster_overlap(cluster: ClusterInfo | None, *, change_cluster_ids: list[str], change_prompts: list[str],
                    text_blob: str) -> ClusterOverlap:
    if cluster is None:
        return ClusterOverlap()
    if cluster.id and str(cluster.id) in {str(c) for c in change_cluster_ids}:
        return ClusterOverlap(100.0, "cluster_id")
    prompts = [_norm(p) for p in cluster.prompts]
    mine = {_norm(p) for p in change_prompts if p}
    if prompts and mine:
        hit = sum(1 for p in prompts if p in mine)
        if hit:
            return ClusterOverlap(round(100.0 * hit / len(prompts), 1), "prompts")
    topic = _norm(cluster.topic)
    if topic and len(topic) >= 4 and topic in _norm(text_blob):
        return ClusterOverlap(100.0, "topic_mention")
    return ClusterOverlap()
