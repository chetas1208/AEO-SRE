"""Profound payloads -> plain dataclasses. Profound-specific structures do not leave this package.

Metric names follow the detector contract (app/incidents/detector.py): visibility, citation_share,
competitor_share (competitor in `competitor`), prompt_volume, accuracy, factual_conflicts, avg_position.
Values are passed through unmodified: Profound documents neither a 0-1 vs 0-100 scale for scores nor a
unit for `accuracy`, so the scale is left for the detector's auto-detection (marked unverified).
Rows missing a required value are dropped, never defaulted.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Any

from .timeutil import et_midnight_utc


@dataclass
class NormalizedSignal:
    kind: str
    metric: str
    value: float
    observed_at: datetime
    baseline: float | None = None
    prompt: str | None = None
    prompt_id: str | None = None
    cluster: str | None = None  # Profound topic name -> PromptCluster.topic
    engine: str | None = None  # AI model/answer engine
    persona: str | None = None
    region: str | None = None
    competitor: str | None = None
    raw_ref: str | None = None
    segment: str | None = None  # series discriminator, e.g. "model=ChatGPT" (None = headline series)
    extra: dict[str, Any] = field(default_factory=dict)  # trimmed original row

    @property
    def source(self) -> str:
        return ("profound" if not self.segment else f"profound:{self.segment}")[:128]


@dataclass
class NormalizedPrompt:
    id: str
    text: str
    topic: str | None
    status: str
    language: str | None = None
    regions: list[str] = field(default_factory=list)
    personas: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    analysis_types: list[str] = field(default_factory=list)


@dataclass
class NormalizedAsset:
    id: str
    name: str
    website: str | None
    alternate_domains: list[str]
    is_owned: bool


@dataclass
class NormalizedCategory:
    id: str
    name: str
    organization_id: str | None


def _name(v: Any) -> str | None:
    if v is None:
        return None
    if isinstance(v, dict):
        v = v.get("name") or v.get("id")
    return str(v) if v not in (None, "") else None


def _id(v: Any) -> str | None:
    return str(v["id"]) if isinstance(v, dict) and v.get("id") else None


def _num(v: Any) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _rows(payload: Any) -> list[dict[str, Any]]:
    rows = payload.get("data") if isinstance(payload, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _observed(row: dict[str, Any], default: datetime) -> datetime:
    d = row.get("date")
    if not d:
        return default
    s = str(d)
    try:
        if len(s) <= 10:
            return et_midnight_utc(s)
        dt = datetime.fromisoformat(s)
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except ValueError:
        return default


def _segment(engine, region, persona, extra: str | None = None, prompt: str | None = None) -> str | None:
    pairs = (("model", engine), ("region", region), ("persona", persona), ("domain", extra), ("prompt", prompt))
    return ";".join(f"{k}={v}" for k, v in pairs if v) or None


def _dims(row: dict[str, Any]) -> dict[str, Any]:
    prompt = row.get("prompt")
    return {
        "engine": _name(row.get("model")),
        "region": _name(row.get("region")),
        "persona": _name(row.get("persona")),
        "cluster": _name(row.get("topic")),
        "prompt": _name(prompt),
        "prompt_id": _id(prompt) if isinstance(prompt, dict) else None,
    }


def _trim(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if v is not None}


def _pkey(d: dict[str, Any]) -> str | None:
    return d["prompt_id"] or (d["prompt"][:60] if d["prompt"] else None)


def normalize_visibility(
    payload: Any, *, raw_ref: str | None = None, default_observed_at: datetime | None = None,
    competitor_names: set[str] | None = None,
) -> list[NormalizedSignal]:
    """v2 visibility rows -> owned: visibility / share_of_voice / avg_position; non-owned: competitor_share."""
    default = default_observed_at or datetime.now(UTC)
    out: list[NormalizedSignal] = []
    for row in _rows(payload):
        asset = row.get("asset") if isinstance(row.get("asset"), dict) else {}
        owned = asset.get("owned")
        if competitor_names and asset.get("name") in competitor_names:
            owned = False
        d = _dims(row)
        at = _observed(row, default)
        seg = _segment(d["engine"], d["region"], d["persona"], prompt=_pkey(d))
        base = dict(observed_at=at, raw_ref=raw_ref, segment=seg, extra=_trim(row), **d)
        if owned is False:
            sov = _num(row.get("share_of_voice"))
            name = asset.get("name")
            if sov is not None and name:
                out.append(NormalizedSignal(kind="competitor", metric="competitor_share", value=sov, competitor=name, **base))
            continue
        for key, metric, kind in (
            ("visibility_score", "visibility", "visibility"),
            ("share_of_voice", "share_of_voice", "visibility"),
            ("average_position", "avg_position", "visibility"),
        ):
            v = _num(row.get(key))
            if v is not None:
                out.append(NormalizedSignal(kind=kind, metric=metric, value=v, **base))
    return out


def normalize_citations(
    payload: Any, *, raw_ref: str | None = None, default_observed_at: datetime | None = None
) -> list[NormalizedSignal]:
    default = default_observed_at or datetime.now(UTC)
    out: list[NormalizedSignal] = []
    for row in _rows(payload):
        d = _dims(row)
        at = _observed(row, default)
        domain = row.get("domain") if isinstance(row.get("domain"), str) else None
        seg = _segment(d["engine"], d["region"], d["persona"], domain, _pkey(d))
        extra = _trim(row)
        base = dict(kind="citation", observed_at=at, raw_ref=raw_ref, segment=seg, extra=extra, **d)
        share = _num(row.get("citation_share"))
        if share is not None:
            out.append(NormalizedSignal(metric="citation_share", value=share, **base))
        cnt = _num(row.get("count"))
        if cnt is not None:
            out.append(NormalizedSignal(metric="citation_count", value=cnt, **base))
    return out


def normalize_fanouts(
    payload: Any, *, raw_ref: str | None = None, default_observed_at: datetime | None = None
) -> list[NormalizedSignal]:
    """v2 query-fanout rows. A row without a query or a share is dropped. Values are not scaled."""
    default = default_observed_at or datetime.now(UTC)
    out: list[NormalizedSignal] = []
    for row in _rows(payload):
        share = _num(row.get("share"))
        query = row.get("query") if isinstance(row.get("query"), str) and row.get("query").strip() else None
        if share is None or query is None:
            continue
        d = _dims(row)
        extra = _trim(row)
        extra["query"] = query
        out.append(NormalizedSignal(
            kind="query_fanout", metric="fanout_share", value=share, observed_at=_observed(row, default),
            prompt=d["prompt"], prompt_id=d["prompt_id"], cluster=d["cluster"], engine=d["engine"],
            persona=d["persona"], region=d["region"], raw_ref=raw_ref,
            segment=_segment(d["engine"], d["region"], d["persona"], None, query), extra=extra,
        ))
    return out


def normalize_factcheck(
    payload: Any, *, raw_ref: str | None = None, default_observed_at: datetime | None = None
) -> list[NormalizedSignal]:
    default = default_observed_at or datetime.now(UTC)
    out: list[NormalizedSignal] = []
    for row in _rows(payload):
        d = _dims(row)
        at = _observed(row, default)
        seg = _segment(d["engine"], d["region"], d["persona"], prompt=_pkey(d))
        base = dict(kind="factcheck", observed_at=at, raw_ref=raw_ref, segment=seg, extra=_trim(row), **d)
        acc = _num(row.get("accuracy"))
        if acc is not None:
            out.append(NormalizedSignal(metric="accuracy", value=acc, **base))
        bad = _num(row.get("inaccurate"))
        if bad is not None:
            out.append(NormalizedSignal(metric="factual_conflicts", value=bad, **base))
    return out


def normalize_volume(
    payload: Any, *, keyword: str, cluster: str | None = None, raw_ref: str | None = None
) -> list[NormalizedSignal]:
    """Weekly rows only form the headline `prompt_volume` series (summed over countries/platforms per date);
    other frequencies get their own series so weekly and monthly projections are never mixed."""
    buckets: dict[tuple[str, str], float] = {}
    for row in _rows(payload):
        v = _num(row.get("volume"))
        d = row.get("date")
        if v is None or not d:
            continue
        freq = str(row.get("frequency") or "unknown").lower()
        key = (freq, str(d)[:10])
        buckets[key] = buckets.get(key, 0.0) + v
    out = []
    for (freq, d), v in sorted(buckets.items()):
        try:
            at = et_midnight_utc(date.fromisoformat(d))
        except ValueError:
            continue
        seg = None if freq.startswith("week") else f"volume_{freq}"
        out.append(NormalizedSignal(
            kind="prompt_volume", metric="prompt_volume", value=v, observed_at=at, cluster=cluster,
            prompt=keyword, raw_ref=raw_ref, segment=seg, extra={"keyword": keyword, "frequency": freq},
        ))
    return out


def normalize_categories(payload: Any) -> list[NormalizedCategory]:
    items = payload if isinstance(payload, list) else []
    return [
        NormalizedCategory(str(c["id"]), str(c.get("name") or c.get("internal_name") or c["id"]),
                           (c.get("organization") or {}).get("id"))
        for c in items if isinstance(c, dict) and c.get("id")
    ]


def normalize_assets(payload: Any) -> list[NormalizedAsset]:
    items = payload if isinstance(payload, list) else []
    return [
        NormalizedAsset(str(a["id"]), str(a.get("name") or ""), a.get("website"),
                        [str(x) for x in (a.get("alternate_domains") or [])], bool(a.get("is_owned")))
        for a in items if isinstance(a, dict) and a.get("id")
    ]


def _names(p: dict[str, Any], key: str) -> list[str]:
    return [n for n in (_name(x) for x in (p.get(key) or [])) if n]


def normalize_prompts(payload: Any) -> list[NormalizedPrompt]:
    out = []
    for p in _rows(payload):
        if not p.get("id") or not p.get("prompt"):
            continue
        out.append(NormalizedPrompt(
            id=str(p["id"]), text=str(p["prompt"]), topic=_name(p.get("topic")), status=str(p.get("status") or "active"),
            language=p.get("language"), regions=_names(p, "regions"), personas=_names(p, "personas"),
            platforms=_names(p, "platforms"), tags=_names(p, "tags"), analysis_types=[str(x) for x in p.get("analysis_types") or []],
        ))
    return out


def normalize_topics(payload: Any) -> list[str]:
    items = payload if isinstance(payload, list) else []
    return [str(t["name"]) for t in items if isinstance(t, dict) and t.get("name") and t.get("status", "active") == "active"]


def normalize_personas(payload: Any) -> list[str]:
    return [n for n in (_name(p) for p in _rows(payload)) if n]


def domain_of(value: str | None) -> str:
    """lower-case host without scheme/www/path, for matching an org domain to an asset website."""
    v = (value or "").strip().lower()
    for pre in ("https://", "http://"):
        v = v.removeprefix(pre)
    v = v.split("/")[0].split("?")[0]
    return v.removeprefix("www.")
