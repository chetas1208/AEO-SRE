"""Normalize a Profound answer row and diff two runs. Deterministic. No network.

Rows are whatever `POST /v2/prompts/answers` returned. Missing fields stay empty.
A row is not an incident by itself.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit


@dataclass(frozen=True)
class AnswerSnapshot:
    provider: str
    run_id: str | None
    prompt_id: str | None
    prompt: str | None
    model: str | None
    observed_on: str | None
    persona: str | None
    topic: str | None
    response: str | None
    mentions: tuple[str, ...]
    citations: tuple[str, ...]
    search_queries: tuple[str, ...]


def _str(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "prompt", "text"):
            if isinstance(value.get(key), str) and value[key].strip():
                return value[key].strip()
    return None


def _strs(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    out = []
    for item in value:
        text = _str(item)
        if text:
            out.append(text)
    return tuple(out)


def snapshot_from_row(row: dict[str, Any]) -> AnswerSnapshot | None:
    if not isinstance(row, dict):
        return None
    prompt = _str(row.get("prompt"))
    if prompt is None and row.get("run_id") is None:
        return None
    return AnswerSnapshot(
        provider="profound",
        run_id=_str(row.get("run_id")),
        prompt_id=_str(row.get("prompt_id")),
        prompt=prompt,
        model=_str(row.get("model")),
        observed_on=_str(row.get("date")),
        persona=_str(row.get("persona")),
        topic=_str(row.get("topic")),
        response=_str(row.get("response")),
        mentions=_strs(row.get("mentions")),
        citations=_strs(row.get("citations")),
        search_queries=_strs(row.get("search_queries")),
    )


def _host(url: str) -> str:
    if "://" not in url:
        return url.lower()
    host = urlsplit(url).hostname or url
    return host.lower()


def citation_diff(before: tuple[str, ...] | list[str], after: tuple[str, ...] | list[str]) -> dict[str, list[str]]:
    b, a = set(before), set(after)
    return {
        "added_pages": sorted(a - b),
        "removed_pages": sorted(b - a),
        "added_domains": sorted({_host(u) for u in a - b} - {_host(u) for u in b}),
        "removed_domains": sorted({_host(u) for u in b - a} - {_host(u) for u in a}),
    }


def query_diff(before: tuple[str, ...] | list[str], after: tuple[str, ...] | list[str],
               competitors: list[str] | None = None) -> dict[str, Any]:
    b, a = {q.lower() for q in before}, {q.lower() for q in after}
    new = sorted(a - b)
    emerged = []
    for name in competitors or []:
        token = name.strip().lower()
        if len(token) < 3:
            continue
        if any(token in q for q in new):
            emerged.append(name)
    return {"added_queries": new, "removed_queries": sorted(b - a), "competitor_emerged": emerged}


def snapshots_known_at(snaps: list[AnswerSnapshot], as_of: datetime) -> list[AnswerSnapshot]:
    """Drop snapshots dated after `as_of`. A missing date is excluded so a replay cannot assume it is in the past."""
    day = as_of.date().isoformat()
    return [s for s in snaps if s.observed_on is not None and s.observed_on <= day]


def answer_diff(before: AnswerSnapshot, after: AnswerSnapshot, *, competitors: list[str] | None = None) -> dict[str, Any]:
    """Meaningful changes only. Identical mentions, citations, and queries produce an empty change list."""
    changes: list[str] = []
    lost = [m for m in before.mentions if m not in after.mentions]
    gained = [m for m in after.mentions if m not in before.mentions]
    if lost:
        changes.append("mention_removed")
    if gained:
        changes.append("mention_added")
    cites = citation_diff(before.citations, after.citations)
    if cites["added_pages"] or cites["removed_pages"]:
        changes.append("citation_set_changed")
    queries = query_diff(before.search_queries, after.search_queries, competitors)
    if queries["competitor_emerged"]:
        changes.append("competitor_query_emerged")
    elif queries["added_queries"]:
        changes.append("search_query_added")
    return {
        "run_id_before": before.run_id, "run_id_after": after.run_id,
        "changes": changes, "mentions_removed": lost, "mentions_added": gained,
        "citations": cites, "queries": queries,
    }
