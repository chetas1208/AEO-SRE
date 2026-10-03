"""Incident detector: rolling baseline + change thresholds + severity aggregation. No LLM.

Pure core: `detect_from_series(points, ...)` works on plain dataclasses (no DB) and returns `IncidentDraft`s.
DB shell: `detect_incidents(session, org_id)` reads `Signal` rows, runs the core, drops drafts that duplicate an
open incident, persists new `Incident` rows (flush only; the caller commits) and returns them.

Signal contract (what the detector reads; metric names are matched case-insensitively via `METRIC_ALIASES`,
falling back to `Signal.kind` when `Signal.metric` is not recognised):

  visibility, citation_share, competitor_share (raw["competitor"] = subject), prompt_volume,
  accuracy (factual accuracy), factual_conflicts (count), cited_sources (count), lost_sources (count),
  new_competitor_content (count), stale_sources (count), avg_position.

Ratio metrics may be fractions (0..1) or percentages (0..100); fractions are auto-detected per series and shown
in percentage points. Optional raw hints: raw["buyer_intent"] (0..1), raw["persona"].

Method per series (metric, cluster, subject, source): the latest `current_points` value(s) are compared with the
median of the preceding `baseline_window` points. A change fires only if (a) its adverse size passes the absolute
minimum, (b) it passes the relative minimum, and (c) when a rolling baseline exists, its robust z-score
(median/MAD, with a noise floor tied to the absolute minimum) passes `z_min`. With fewer than
`min_baseline_points` of history the provider-supplied `Signal.baseline` is used (no z-score, lower confidence);
"event" metrics (counts of things that happened) treat missing history as a zero baseline. Anomalies are grouped
per prompt cluster; visibility drops and competitor gains in one cluster merge into a single incident.
Thresholds are hand-set config, not learned.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import structlog

from app.domain.enums import IncidentCategory, IncidentState
from app.incidents.priority import (
    DEFAULT_CONFIG as DEFAULT_PRIORITY_CONFIG,
)
from app.incidents.priority import (
    PriorityConfig,
    PriorityResult,
    buyer_intent_from_prompts,
    compute_priority,
    demand_from_volume,
    displacement_from_competitor_gain,
    feasibility_for_category,
    persona_importance_from_config,
    severity_from_priority,
)
from app.incidents.signature import (
    FANOUT_SIGNAL,
    SIGNAL_FAMILY,
    build_signature,
    common_scope,
    scope_of,
)

log = structlog.get_logger(__name__)

TERMINAL_STATES: frozenset[str] = frozenset(
    {
        IncidentState.CLOSED.value,
        IncidentState.DISMISSED.value,
        IncidentState.FAILED.value,
        IncidentState.REWARDED.value,
    }
)

# ---------------------------------------------------------------------------------------------------------
# Rules


@dataclass(frozen=True)
class MetricRule:
    key: str
    label: str
    category: IncidentCategory
    direction: Literal["up", "down"]  # which way the metric moves when things go wrong
    kind: Literal["ratio", "count", "position"]
    min_abs: float  # minimum adverse change in display units (pp for ratio, items for count)
    min_rel: float  # minimum adverse change relative to baseline (0.1 = 10%)
    full_scale: float  # adverse change (display units, or relative if rel_scale) that maps to magnitude 1.0
    unit: str
    z_min: float = 3.0
    event: bool = False  # counts of events: no history means a zero baseline
    supporting: bool = False  # only reported alongside a primary anomaly in the same cluster
    rel_scale: bool = False  # magnitude from relative change (volume spikes)


RULES: dict[str, MetricRule] = {
    r.key: r
    for r in (
        MetricRule("visibility", "Visibility", IncidentCategory.VISIBILITY_DROP, "down", "ratio", 5.0, 0.10, 25.0, "pp"),
        MetricRule("citation_share", "Citation share", IncidentCategory.VISIBILITY_DROP, "down", "ratio", 4.0, 0.10, 20.0, "pp"),
        MetricRule(
            "competitor_share", "Competitor share", IncidentCategory.COMPETITOR_CITATION_GAIN, "up", "ratio", 5.0, 0.15, 30.0, "pp"
        ),
        MetricRule(
            "prompt_volume", "Prompt volume", IncidentCategory.PROMPT_VOLUME_SPIKE, "up", "count", 1.0, 0.50, 2.0, "count",
            rel_scale=True,
        ),
        MetricRule("accuracy", "Factual accuracy", IncidentCategory.FACTUAL_CONFLICT, "down", "ratio", 10.0, 0.10, 30.0, "pp"),
        MetricRule(
            "factual_conflicts", "Factual conflicts", IncidentCategory.FACTUAL_CONFLICT, "up", "count", 1.0, 0.0, 5.0, "count",
            event=True,
        ),
        MetricRule("cited_sources", "Cited sources", IncidentCategory.LOST_CITATION_SOURCE, "down", "count", 2.0, 0.20, 5.0, "count"),
        MetricRule(
            "lost_sources", "Lost citation sources", IncidentCategory.LOST_CITATION_SOURCE, "up", "count", 1.0, 0.0, 5.0, "count",
            event=True,
        ),
        MetricRule(
            "new_competitor_content", "New competitor pages", IncidentCategory.NEW_COMPETITOR_CONTENT, "up", "count", 1.0, 0.0, 3.0,
            "count", event=True,
        ),
        MetricRule(
            "stale_sources", "Stale cited sources", IncidentCategory.STALE_INFORMATION, "up", "count", 1.0, 0.0, 5.0, "count",
            event=True,
        ),
        MetricRule(
            "avg_position", "Average position", IncidentCategory.VISIBILITY_DROP, "up", "position", 1.0, 0.20, 5.0, "pos",
            supporting=True,
        ),
    )
}

METRIC_ALIASES: dict[str, str] = {
    "visibility": "visibility", "visibility_score": "visibility", "visibility_pct": "visibility",
    "citation_share": "citation_share", "citations_share": "citation_share",
    "competitor_share": "competitor_share", "competitor_citation_share": "competitor_share",
    "competitor_visibility": "competitor_share",
    "prompt_volume": "prompt_volume", "volume": "prompt_volume",
    "accuracy": "accuracy", "factual_accuracy": "accuracy", "fact_check_score": "accuracy",
    "factual_conflicts": "factual_conflicts", "factual_conflict_count": "factual_conflicts",
    "cited_sources": "cited_sources", "citation_source_count": "cited_sources", "source_count": "cited_sources",
    "lost_sources": "lost_sources", "lost_citation_sources": "lost_sources",
    "new_competitor_content": "new_competitor_content", "competitor_new_pages": "new_competitor_content",
    "competitor_content_changes": "new_competitor_content",
    "stale_sources": "stale_sources", "stale_citations": "stale_sources",
    "avg_position": "avg_position", "average_position": "avg_position",
}

# Categories that describe one phenomenon (share being taken from us) and are merged per cluster.
# Visibility regression + citation loss + competitor gain (+ a material query-fanout change) that share a cluster and
# scope are ONE incident, not four.
_FAMILY: dict[IncidentCategory, str] = {
    IncidentCategory.VISIBILITY_DROP: "displacement",
    IncidentCategory.COMPETITOR_CITATION_GAIN: "displacement",
    IncidentCategory.LOST_CITATION_SOURCE: "displacement",
}


def _provenance(sources: list[str]) -> str:
    """live only when every cited signal says Profound. A fixture must never be labeled live."""
    if not sources:
        return "unknown"
    if all(s == "dev_fixture" or s.startswith("dev_fixture") for s in sources):
        return "test_fixture"
    if all(s == "profound" or s.startswith("profound") for s in sources):
        return "live"
    return "mixed"


def dedup_family(category: IncidentCategory | str) -> str:
    cat = IncidentCategory(category)
    return _FAMILY.get(cat, cat.value)


# ---------------------------------------------------------------------------------------------------------
# Data classes


@dataclass(frozen=True)
class SignalPoint:
    metric: str
    value: float
    observed_at: datetime
    kind: str = ""
    baseline: float | None = None
    prompt_cluster_id: uuid.UUID | str | None = None
    source: str = "profound"
    raw: Mapping[str, Any] = field(default_factory=dict)
    signal_id: str | None = None


@dataclass(frozen=True)
class MetricDelta:
    label: str
    before: float | None
    after: float | None
    delta: float | None
    unit: str
    key: str = ""
    delta_pct: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "before": _r(self.before),
            "after": _r(self.after),
            "delta": _r(self.delta),
            "unit": self.unit,
            "key": self.key,
            "delta_pct": _r(self.delta_pct),
        }


@dataclass(frozen=True)
class DetectionConfig:
    baseline_window: int = 14  # max history points used for the rolling baseline
    min_baseline_points: int = 3
    current_points: int = 1  # latest points averaged into the "after" value
    lookback_days: int = 45
    max_metrics: int = 4
    ratio_scale: Literal["auto", "fraction", "percent"] = "auto"
    rules: Mapping[str, MetricRule] = field(default_factory=lambda: RULES)
    priority: PriorityConfig = DEFAULT_PRIORITY_CONFIG


@dataclass
class DetectionContext:
    """Optional org/cluster metadata for naming and priority components."""

    cluster_topics: Mapping[str, str] = field(default_factory=dict)
    cluster_prompts: Mapping[str, Sequence[str]] = field(default_factory=dict)
    personas: Sequence[Any] = field(default_factory=list)


@dataclass
class IncidentDraft:
    title: str
    category: IncidentCategory
    prompt_cluster_id: str | None
    topic: str
    metrics: list[MetricDelta]
    first_observed_at: datetime
    detected_at: datetime
    confidence: float
    summary: str
    priority: PriorityResult
    severity: Any  # app.domain.enums.Severity
    dedup_key: str
    context: dict[str, Any] = field(default_factory=dict)

    @property
    def metrics_json(self) -> list[dict[str, Any]]:
        return [m.to_dict() for m in self.metrics]


@dataclass(frozen=True)
class ExistingIncident:
    category: str
    prompt_cluster_id: str | None
    platform: str | None = None
    persona: str | None = None


@dataclass
class _Eval:
    rule: MetricRule
    subject: str | None
    cluster_id: str | None
    source: str
    before: float
    after: float
    adverse: float
    rel: float
    z: float | None
    baseline_source: str
    n_history: int
    first_observed_at: datetime
    last_observed_at: datetime
    signal_ids: list[str]

    @property
    def delta(self) -> float:
        return self.after - self.before


@dataclass
class _Anomaly:
    ev: _Eval
    magnitude: float
    confidence: float


def _r(x: float | None) -> float | None:
    return None if x is None or not math.isfinite(x) else round(x, 4)


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


# ---------------------------------------------------------------------------------------------------------
# Series evaluation


def resolve_metric(point: SignalPoint) -> str | None:
    for name in (point.metric, point.kind):
        key = (name or "").strip().lower()
        if key in METRIC_ALIASES:
            return METRIC_ALIASES[key]
    return None


def _subject(rule: MetricRule, point: SignalPoint) -> str | None:
    if rule.key == "competitor_share":
        v = point.raw.get("competitor") or point.raw.get("subject") or point.raw.get("domain")
        return str(v) if v else None
    return None


def _scale_for(rule: MetricRule, values: Sequence[float], cfg: DetectionConfig) -> float:
    if rule.kind != "ratio":
        return 1.0
    if cfg.ratio_scale == "fraction":
        return 100.0
    if cfg.ratio_scale == "percent":
        return 1.0
    return 100.0 if values and max(abs(v) for v in values) <= 1.0 else 1.0


def _evaluate(
    rule: MetricRule, subject: str | None, cluster_id: str | None, source: str,
    pts: list[SignalPoint], cfg: DetectionConfig,
) -> _Eval | None:
    pts = sorted(pts, key=lambda p: _aware(p.observed_at))
    k = max(1, cfg.current_points)
    last = pts[-1]
    all_vals = [p.value for p in pts] + ([last.baseline] if last.baseline is not None else [])
    scale = _scale_for(rule, all_vals, cfg)
    vals = [p.value * scale for p in pts]
    history = vals[:-k][-cfg.baseline_window :]
    after = statistics.fmean(vals[-k:])

    z_base_sigma: float | None = None
    if len(history) >= cfg.min_baseline_points:
        before = statistics.median(history)
        mad = statistics.median(abs(v - before) for v in history)
        z_base_sigma = 1.4826 * mad
        baseline_source = "rolling"
    elif last.baseline is not None:
        before, baseline_source = last.baseline * scale, "provided"
    elif rule.event:
        before = statistics.median(history) if history else 0.0
        baseline_source = "rolling" if history else "assumed_zero"
    else:
        return None

    change = after - before
    adverse = change if rule.direction == "up" else -change
    if adverse < rule.min_abs or adverse <= 0:
        return None
    rel = adverse / abs(before) if before else math.inf
    if rel < rule.min_rel:
        return None
    z: float | None = None
    if z_base_sigma is not None:
        sigma = max(z_base_sigma, rule.min_abs / 4.0, 0.02 * abs(before), 1e-9)
        z = adverse / sigma
        if z < rule.z_min:
            return None

    # Walk back through the contiguous run of points that already deviate in the adverse direction.
    half = 0.5 * rule.min_abs
    first_idx = len(pts) - 1
    for i in range(len(pts) - 1, -1, -1):
        dev = (vals[i] - before) if rule.direction == "up" else (before - vals[i])
        if dev >= half:
            first_idx = i
        else:
            break
    return _Eval(
        rule=rule, subject=subject, cluster_id=cluster_id, source=source,
        before=before, after=after, adverse=adverse, rel=rel, z=z, baseline_source=baseline_source,
        n_history=len(history), first_observed_at=_aware(pts[first_idx].observed_at),
        last_observed_at=_aware(last.observed_at),
        signal_ids=[p.signal_id for p in pts[first_idx:] if p.signal_id],
    )


def _magnitude(ev: _Eval) -> float:
    r = ev.rule
    basis = (ev.rel if math.isfinite(ev.rel) else r.full_scale) if r.rel_scale else ev.adverse
    return max(0.0, min(1.0, basis / r.full_scale))


def _confidence(ev: _Eval, cfg: DetectionConfig) -> float:
    if ev.z is None:
        return 0.30 if ev.baseline_source == "assumed_zero" else 0.35
    coverage = min(1.0, ev.n_history / 7.0)
    strength = min(1.0, ev.z / 6.0)
    return round(0.4 * coverage + 0.6 * strength, 3)


# ---------------------------------------------------------------------------------------------------------
# Core


def historical_replay(points: Sequence[SignalPoint], *, as_of: datetime, context: DetectionContext | None = None,
                      config: DetectionConfig | None = None) -> dict[str, Any]:
    """Detect using only observations at or before `as_of`. Does not write incidents or fetch anything.

    The result is a HISTORICAL REPLAY. It is not a live incident and must not be shown as one.
    """
    drafts = detect_from_series(points, context=context, config=config, now=as_of)
    return {
        "mode": "historical_replay",
        "as_of": _aware(as_of).isoformat(),
        "would_detect": len(drafts),
        "categories": [d.category.value for d in drafts],
        "titles": [d.title for d in drafts],
    }


def detect_from_series(
    points: Iterable[SignalPoint],
    *,
    context: DetectionContext | None = None,
    config: DetectionConfig | None = None,
    now: datetime | None = None,
) -> list[IncidentDraft]:
    """Detect incident drafts from plain signal points. Deterministic; no I/O; no LLM.

    Points observed after `now` are ignored, so a historical `now` cannot see the future.
    """
    cfg = config or DetectionConfig()
    ctx = context or DetectionContext()
    now = _aware(now or datetime.now(UTC))
    points = [p for p in points if _aware(p.observed_at) <= now]

    series: dict[tuple[str, str | None, str | None, str], list[SignalPoint]] = defaultdict(list)
    for p in points:
        if not math.isfinite(p.value):
            continue
        key = resolve_metric(p)
        if key is None or key not in cfg.rules:
            continue
        rule = cfg.rules[key]
        cid = str(p.prompt_cluster_id) if p.prompt_cluster_id is not None else None
        series[(key, cid, _subject(rule, p), p.source)].append(p)

    evals: dict[tuple[str, str | None, str | None, str], _Eval | None] = {}
    for (key, cid, subj, src), pts in series.items():
        evals[(key, cid, subj, src)] = _evaluate(cfg.rules[key], subj, cid, src, pts, cfg)

    # Group by (cluster, family, platform/persona scope). A segment-only regression (ChatGPT-only, CISO-only) is its
    # own incident labeled with that scope; segment anomalies fold into the cluster-wide incident only when a
    # cluster-wide anomaly of the same family exists.
    groups: dict[tuple[str | None, str, tuple[str | None, str | None]], list[_Anomaly]] = defaultdict(list)
    for ev in evals.values():
        if ev is None:
            continue
        groups[(ev.cluster_id, dedup_family(ev.rule.category), scope_of(ev.source))].append(
            _Anomaly(ev, _magnitude(ev), _confidence(ev, cfg))
        )
    for key in [k for k in groups if k[2] != (None, None) and (k[0], k[1], (None, None)) in groups]:
        groups[(key[0], key[1], (None, None))].extend(groups.pop(key))

    fanout = _fanout_by_cluster(points, ctx)
    drafts: list[IncidentDraft] = []
    for (cid, _family, _scope), anomalies in groups.items():
        primary = [a for a in anomalies if not a.ev.rule.supporting]
        if not primary:
            continue
        drafts.append(_build_draft(cid, anomalies, primary, series, evals, ctx, cfg, now, fanout.get(cid or "", [])))
    drafts.sort(key=lambda d: d.priority.score, reverse=True)
    return drafts


def _fanout_by_cluster(points: Sequence[SignalPoint], ctx: DetectionContext) -> dict[str, list[Any]]:
    """Material query-fanout shifts per cluster (corroboration only: never raises an incident by itself)."""
    from app.investigation.fanout import material_fanout_shifts

    rows: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for p in points:
        if (p.kind or "").lower() != "query_fanout" or p.prompt_cluster_id is None:
            continue
        raw = p.raw or {}
        query = (raw.get("row") or {}).get("query") or raw.get("query")
        rows[str(p.prompt_cluster_id)].append(
            {"query": query, "prompt": raw.get("prompt"), "observed_at": _aware(p.observed_at), "share": p.value}
        )
    return {cid: shifts for cid, r in rows.items() if (shifts := material_fanout_shifts(r))}


def _category_for(primary: list[_Anomaly]) -> IncidentCategory:
    cats = {a.ev.rule.category for a in primary}
    if IncidentCategory.VISIBILITY_DROP in cats:
        return IncidentCategory.VISIBILITY_DROP
    return max(primary, key=lambda a: a.magnitude).ev.rule.category


def _fmt(v: float | None, unit: str) -> str:
    if v is None:
        return "n/a"
    if unit == "pp":
        return f"{v:.0f}%"
    return f"{v:g}" if abs(v) < 1000 else f"{v:,.0f}"


def _delta_metric(ev: _Eval) -> MetricDelta:
    r = ev.rule
    label = r.label if not ev.subject else f"{r.label} ({ev.subject})"
    pct = (ev.delta / abs(ev.before) * 100.0) if ev.before else None
    return MetricDelta(label, ev.before, ev.after, ev.delta, r.unit, key=r.key, delta_pct=pct)


def _title(category: IncidentCategory, topic: str, primary: list[_Anomaly], all_anoms: list[_Anomaly]) -> str:
    best = max(primary, key=lambda a: a.magnitude).ev
    r = best.rule
    if category is IncidentCategory.VISIBILITY_DROP:
        vis = [a.ev for a in primary if a.ev.rule.key == "visibility"] or [best]
        e = max(vis, key=lambda x: x.adverse)
        return f"{topic}: {e.rule.label.lower()} dropped {_fmt(e.before, e.rule.unit)} -> {_fmt(e.after, e.rule.unit)}"
    if category is IncidentCategory.COMPETITOR_CITATION_GAIN:
        who = best.subject or "competitor"
        return f"{topic}: {who} gained {best.delta:.0f}pp share ({_fmt(best.before, 'pp')} -> {_fmt(best.after, 'pp')})"
    if category is IncidentCategory.FACTUAL_CONFLICT:
        if r.key == "factual_conflicts":
            return f"{topic}: {best.after:g} factual conflict(s) detected (baseline {best.before:g})"
        return f"{topic}: factual accuracy dropped {_fmt(best.before, 'pp')} -> {_fmt(best.after, 'pp')}"
    if category is IncidentCategory.LOST_CITATION_SOURCE:
        n = best.adverse
        return f"{topic}: lost {n:g} citation source(s)"
    if category is IncidentCategory.PROMPT_VOLUME_SPIKE:
        pct = best.rel * 100 if math.isfinite(best.rel) else float("inf")
        return f"{topic}: prompt volume up {pct:.0f}% ({best.before:g} -> {best.after:g})"
    if category is IncidentCategory.NEW_COMPETITOR_CONTENT:
        return f"{topic}: {best.after:g} new competitor page(s) detected"
    return f"{topic}: {best.after:g} stale source(s) cited"


def _summary(category: IncidentCategory, topic: str, metrics: list[MetricDelta], anoms: list[_Anomaly]) -> str:
    parts = []
    for m in metrics:
        sign = "+" if (m.delta or 0) >= 0 else ""
        parts.append(f"{m.label} {_fmt(m.before, m.unit)} -> {_fmt(m.after, m.unit)} ({sign}{m.delta:.1f}{m.unit if m.unit != 'count' else ''})")
    sources = sorted({a.ev.baseline_source for a in anoms})
    return (
        f"{category.value.replace('_', ' ').capitalize()} on '{topic}'. " + "; ".join(parts)
        + f". Baseline: {', '.join(sources)}. Detected by thresholds on observed signals; cause not yet investigated."
    )


def _latest_value(
    series: Mapping[tuple[str, str | None, str | None, str], list[SignalPoint]], key: str, cid: str | None
) -> float | None:
    vals = [
        max(pts, key=lambda p: _aware(p.observed_at)).value
        for (k, c, _s, _src), pts in series.items()
        if k == key and c == cid
    ]
    return max(vals) if vals else None


def _build_draft(
    cid: str | None,
    anomalies: list[_Anomaly],
    primary: list[_Anomaly],
    series: Mapping[tuple[str, str | None, str | None, str], list[SignalPoint]],
    evals: Mapping[tuple[str, str | None, str | None, str], _Eval | None],
    ctx: DetectionContext,
    cfg: DetectionConfig,
    now: datetime,
    fanout_shifts: Sequence[Any] = (),
) -> IncidentDraft:
    category = _category_for(primary)
    topic = ctx.cluster_topics.get(cid, "") if cid else ""
    topic = topic or ("Overall" if cid is None else "Unnamed topic")

    ordered = sorted(anomalies, key=lambda a: (a.ev.rule.category is not category, -a.magnitude))
    metric_evals = [a.ev for a in ordered][: cfg.max_metrics]
    metrics = [_delta_metric(e) for e in metric_evals]
    # Add current prompt volume as context when there is room (no threshold gate; shown as observed).
    if len(metrics) < cfg.max_metrics and not any(m.key == "prompt_volume" for m in metrics):
        for (k, c, _s, _src), pts in series.items():
            ev = evals.get((k, c, _s, _src))
            if k == "prompt_volume" and c == cid and ev is None and len(pts) >= 2:
                p = sorted(pts, key=lambda x: _aware(x.observed_at))
                before = statistics.median(x.value for x in p[:-1][-cfg.baseline_window :])
                after = p[-1].value
                pct = (after - before) / before * 100 if before else None
                metrics.append(MetricDelta("Prompt volume", before, after, after - before, "count", key=k, delta_pct=pct))
                break

    # severity aggregation: strongest anomaly plus a small corroboration bonus per extra independent anomaly
    mags = sorted((a.magnitude for a in anomalies), reverse=True)
    severity_component = min(1.0, mags[0] + 0.1 * (len(mags) - 1))
    confidence = round(statistics.fmean(a.confidence for a in anomalies), 3)
    signal_families = {SIGNAL_FAMILY[a.ev.rule.key] for a in anomalies if a.ev.rule.key in SIGNAL_FAMILY}
    if fanout_shifts:
        signal_families.add(FANOUT_SIGNAL)
    # independent corroborating signal families raise confidence a little; capped, never decisive
    confidence = round(min(1.0, confidence + 0.05 * max(0, len(signal_families) - 1)), 3)

    volume = _latest_value(series, "prompt_volume", cid)
    comp_gain = None
    comp_evals = [(k, v) for k, v in evals.items() if k[0] == "competitor_share" and k[1] == cid]
    if comp_evals:
        comp_gain = max((v.delta if v else 0.0) for _k, v in comp_evals)
        # also show competitors whose share rose a little without crossing the incident threshold as 0 gain
    sig_raws = [p.raw for pts in series.values() for p in pts if (p.prompt_cluster_id and str(p.prompt_cluster_id) == cid) or (cid is None and p.prompt_cluster_id is None)]
    intent_raw = next((r["buyer_intent"] for r in reversed(sig_raws) if isinstance(r.get("buyer_intent"), (int, float))), None)
    persona_name = next((str(r["persona"]) for r in reversed(sig_raws) if r.get("persona")), None)
    prompts = ctx.cluster_prompts.get(cid or "", []) if cid else []
    intent = intent_raw if intent_raw is not None else buyer_intent_from_prompts(prompts)
    persona = persona_importance_from_config(ctx.personas, persona_name)

    priority = compute_priority(
        prompt_demand=demand_from_volume(volume, cfg.priority),
        buyer_intent=intent,
        incident_severity=severity_component,
        persona_importance=persona,
        competitive_displacement=displacement_from_competitor_gain(comp_gain, cfg.priority),
        evidence_confidence=confidence,
        remediation_feasibility=feasibility_for_category(category, cfg.priority),
        config=cfg.priority,
        sources={
            "prompt_demand": "measured",
            "buyer_intent": "measured" if intent_raw is not None else "heuristic",
            "incident_severity": "measured",
            "persona_importance": "config",
            "competitive_displacement": "measured",
            "evidence_confidence": "measured",
            "remediation_feasibility": "config",
        },
        notes={
            "evidence_confidence": "detection-signal confidence only; recomputed after investigation",
            "buyer_intent": "keyword heuristic on cluster prompts" if intent_raw is None and intent is not None else "",
            "remediation_feasibility": "hand-set prior per category",
        },
    )
    severity = severity_from_priority(priority.score, cfg.priority.thresholds)

    first_observed = min(a.ev.first_observed_at for a in anomalies)
    sources = sorted({a.ev.source for a in anomalies if a.ev.source})
    competitors = sorted({a.ev.subject for a in anomalies if a.ev.subject})
    primary_metric = max(primary, key=lambda a: a.magnitude).ev.rule.key
    platform, persona = common_scope(a.ev.source for a in primary)
    signature = build_signature(
        incident_type=category.value, family=dedup_family(category), topic=topic, cluster_id=cid,
        platform=platform, persona=persona, competitors=competitors, primary_metric=primary_metric,
        direction="negative", metrics=[m.key for m in metrics if m.key], signal_families=signal_families,
    )
    dedup_key = f"{dedup_family(category)}:{cid or 'org'}"  # stable shape; the scoped key is signature_key
    context: dict[str, Any] = {
        "dedup_key": dedup_key,
        "family": dedup_family(category),
        "provenance": _provenance(sources),
        "signature": signature,
        "scope": {"platform": signature["platform"], "persona": signature["persona"],
                  "generalizes_beyond_scope": False},
        "correlated_signals": sorted(signal_families),
        "fanout_shifts": [
            {"kind": f.kind, "query": f.query, "competitor": f.competitor, "before": f.before_share,
             "after": f.after_share} for f in list(fanout_shifts)[:5]
        ],
        "below_action_threshold": priority.score < cfg.priority.observe_below,
        "unknown_priority_components": priority.unknown_components,
        "signal_ids": sorted({s for a in anomalies for s in a.ev.signal_ids}),
        "detection": [
            {
                "metric": a.ev.rule.key, "subject": a.ev.subject, "source": a.ev.source,
                "baseline_source": a.ev.baseline_source, "baseline_points": a.ev.n_history,
                "z": _r(a.ev.z), "magnitude": round(a.magnitude, 3), "confidence": a.confidence,
                "supporting": a.ev.rule.supporting,
                "last_observed_at": a.ev.last_observed_at.isoformat(),
            }
            for a in anomalies
        ],
        "topic": topic,
    }
    return IncidentDraft(
        title=_title(category, topic, primary, anomalies),
        category=category,
        prompt_cluster_id=cid,
        topic=topic,
        metrics=metrics,
        first_observed_at=first_observed,
        detected_at=now,
        confidence=confidence,
        summary=_summary(category, topic, metrics, anomalies),
        priority=priority,
        severity=severity,
        dedup_key=dedup_key,
        context=context,
    )


def _scope_key(family: str, cluster: str | None, platform: str | None, persona: str | None) -> tuple:
    return (family, cluster, (platform or "").lower(), (persona or "").lower())


def filter_duplicates(drafts: Iterable[IncidentDraft], existing: Iterable[ExistingIncident]) -> list[IncidentDraft]:
    """Drop drafts that match an open incident on (category family, prompt cluster, platform, persona).

    A persona- or platform-specific regression is not a duplicate of the cluster-wide incident (and vice versa)."""
    taken = {_scope_key(dedup_family(e.category), e.prompt_cluster_id, e.platform, e.persona) for e in existing}
    out: list[IncidentDraft] = []
    for d in drafts:
        sig = d.context.get("signature") or {}
        k = _scope_key(dedup_family(d.category), d.prompt_cluster_id, sig.get("platform"), sig.get("persona"))
        if k in taken:
            log.info("detector.duplicate_skipped", dedup_key=d.dedup_key, title=d.title)
            continue
        taken.add(k)
        out.append(d)
    return out


# ---------------------------------------------------------------------------------------------------------
# DB shell


async def detect_incidents(
    session: Any, org_id: uuid.UUID, *, config: DetectionConfig | None = None, now: datetime | None = None
) -> list[Any]:
    """Read the org's Signals, detect incidents, skip duplicates of open incidents, persist and return new ones.

    Flushes but does not commit. Returns only newly created `Incident` rows."""
    from sqlalchemy import select, text
    from sqlalchemy.exc import IntegrityError

    from app.models.core import Incident, Organization, PromptCluster, Signal

    cfg = config or DetectionConfig()
    now = _aware(now or datetime.now(UTC))
    since = now - timedelta(days=cfg.lookback_days)

    rows = (
        await session.execute(
            select(Signal).where(
                Signal.org_id == org_id,
                Signal.observed_at >= since,
                Signal.observed_at <= now,
            ).order_by(Signal.observed_at)
        )
    ).scalars().all()
    points = [
        SignalPoint(
            metric=s.metric, value=float(s.value), observed_at=_aware(s.observed_at), kind=s.kind,
            baseline=s.baseline, prompt_cluster_id=s.prompt_cluster_id, source=s.source,
            raw=s.raw or {}, signal_id=str(s.id),
        )
        for s in rows
    ]
    clusters = (await session.execute(select(PromptCluster).where(PromptCluster.org_id == org_id))).scalars().all()
    org = (await session.execute(select(Organization).where(Organization.id == org_id))).scalar_one_or_none()
    ctx = DetectionContext(
        cluster_topics={str(c.id): c.topic for c in clusters},
        cluster_prompts={str(c.id): [p if isinstance(p, str) else str(p.get("text", "")) for p in (c.prompts or [])] for c in clusters},
        personas=(org.personas if org else []) or [],
    )
    drafts = detect_from_series(points, context=ctx, config=cfg, now=now)
    if not drafts:
        return []

    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": int(org_id.int % (2**31 - 1)) or 1},
        )
    open_rows = (
        await session.execute(
            select(Incident.category, Incident.prompt_cluster_id, Incident.context).where(
                Incident.org_id == org_id, Incident.state.notin_(TERMINAL_STATES)
            )
        )
    ).all()
    existing = [
        ExistingIncident(
            str(c), str(p) if p else None, ((x or {}).get("signature") or {}).get("platform"),
            ((x or {}).get("signature") or {}).get("persona"),
        )
        for c, p, x in open_rows
    ]
    fresh = filter_duplicates(drafts, existing)

    created = []
    for d in fresh:
        inc = Incident(
            org_id=org_id,
            title=d.title,
            category=d.category.value,
            severity=d.severity.value,
            priority=round(d.priority.score, 2),
            priority_breakdown=d.priority.to_dict(),
            state=IncidentState.DETECTED.value,
            detected_at=d.detected_at,
            first_observed_at=d.first_observed_at,
            prompt_cluster_id=uuid.UUID(d.prompt_cluster_id) if d.prompt_cluster_id else None,
            metrics=d.metrics_json,
            confidence=d.confidence,
            summary=d.summary,
            investigation_status="not_started",
            context=d.context,
        )
        # SAVEPOINT per incident: a concurrent detector that won the fingerprint unique index makes this one a
        # duplicate to skip, not an error.
        try:
            async with session.begin_nested():
                session.add(inc)
                await session.flush()
        except IntegrityError:
            if inc in session:
                session.expunge(inc)
            log.info("detector.duplicate_skipped_by_constraint", title=d.title)
            continue
        created.append(inc)
    log.info("detector.done", org_id=str(org_id), drafts=len(drafts), created=len(created))
    return created
