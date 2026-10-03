"""Control movement: did the whole category move, or only us?

A visibility drop is only attributable to the brand when comparable controls did NOT move the same way. Controls are
(a) tracked competitors, (b) the brand's own other prompt clusters, (c) the brand's other platforms (for a
platform-specific incident). If most controls fell together with us, the movement is category / platform / model wide
and brand-specific causes must not be confirmed. With too few controls the verdict is INCONCLUSIVE (never assumed
brand-specific).
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

MIN_CONTROLS = 2
CATEGORY_WIDE_SHARE = 0.6
BRAND_SPECIFIC_SHARE = 0.25
MIN_MOVE_PP = 2.0
MOVE_RATIO = 0.3  # a control "moved with us" when it fell by >= 30% of our fall (and >= MIN_MOVE_PP)


class ControlVerdict(StrEnum):
    BRAND_SPECIFIC = "brand_specific"
    CATEGORY_WIDE = "category_wide"
    INCONCLUSIVE = "inconclusive"


@dataclass
class ControlSeries:
    name: str
    group: str  # competitor | cluster | platform
    points: list[tuple[datetime, float]]  # (observed_at, value in the control's native scale)


@dataclass
class ControlAssessment:
    verdict: ControlVerdict
    own_delta_pp: float | None
    controls: list[dict[str, Any]] = field(default_factory=list)
    moved: int = 0
    total: int = 0
    note: str = ""

    @property
    def checked(self) -> bool:
        return self.verdict is not ControlVerdict.INCONCLUSIVE

    def to_dict(self) -> dict[str, Any]:
        return {"verdict": self.verdict.value, "own_delta_pp": self.own_delta_pp, "moved": self.moved,
                "total": self.total, "controls": self.controls, "note": self.note, "checked": self.checked}


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _scale(values: Sequence[float]) -> float:
    return 100.0 if values and max(abs(v) for v in values) <= 1.0 else 1.0


def series_delta_pp(points: Sequence[tuple[datetime, float]], at: datetime, baseline_window: int = 14) -> float | None:
    """latest value at/after `at` minus the median of the points before it, in percentage points."""
    at = _aware(at)
    pts = sorted(((_aware(t), v) for t, v in points), key=lambda p: p[0])
    before = [v for t, v in pts if t < at][-baseline_window:]
    after = [v for t, v in pts if t >= at]
    if len(before) < 3 or not after:
        return None
    sc = _scale([v for _, v in pts])
    return (after[-1] - statistics.median(before)) * sc


def assess_control_movement(own_delta_pp: float | None, controls: Sequence[ControlSeries], at: datetime) -> ControlAssessment:
    if own_delta_pp is None or own_delta_pp >= 0:
        return ControlAssessment(ControlVerdict.INCONCLUSIVE, own_delta_pp, note="no adverse own movement to compare")
    need = max(MIN_MOVE_PP, MOVE_RATIO * abs(own_delta_pp))
    rows: list[dict[str, Any]] = []
    for c in controls:
        d = series_delta_pp(c.points, at)
        if d is None:
            continue
        rows.append({"name": c.name, "group": c.group, "delta_pp": round(d, 2), "moved_with_us": d <= -need})
    total = len(rows)
    moved = sum(1 for r in rows if r["moved_with_us"])
    if total < MIN_CONTROLS:
        return ControlAssessment(ControlVerdict.INCONCLUSIVE, round(own_delta_pp, 2), rows, moved, total,
                                 f"only {total} usable control series; category-wide movement cannot be ruled out")
    share = moved / total
    if share >= CATEGORY_WIDE_SHARE:
        return ControlAssessment(ControlVerdict.CATEGORY_WIDE, round(own_delta_pp, 2), rows, moved, total,
                                 f"{moved}/{total} controls fell too: consider platform/model movement before blaming the brand")
    if share <= BRAND_SPECIFIC_SHARE:
        return ControlAssessment(ControlVerdict.BRAND_SPECIFIC, round(own_delta_pp, 2), rows, moved, total,
                                 f"only {moved}/{total} controls fell: the movement is specific to the brand")
    return ControlAssessment(ControlVerdict.INCONCLUSIVE, round(own_delta_pp, 2), rows, moved, total,
                             f"{moved}/{total} controls fell: mixed, neither brand-specific nor category-wide")


async def load_controls(session: Any, incident: Any) -> tuple[float | None, list[ControlSeries]]:
    """(own adverse delta in pp, control series) for the incident, read from persisted Signals only."""
    from sqlalchemy import select

    from app.incidents.signature import scope_of
    from app.models.core import Signal

    ctx = incident.context or {}
    sig = ctx.get("signature") or {}
    own_metric = next((m for m in (incident.metrics or []) if m.get("key") in ("visibility", "citation_share")), None)
    own_delta = None
    if own_metric and own_metric.get("delta") is not None:
        own_delta = float(own_metric["delta"])
    rows = (await session.execute(select(Signal).where(Signal.org_id == incident.org_id))).scalars().all()
    controls: list[ControlSeries] = []
    platform = sig.get("platform")
    by: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    for s in rows:
        raw = s.raw or {}
        if s.kind == "competitor" and (raw.get("competitor") or s.metric):
            if s.metric in ("competitor_share", "share_of_voice", "visibility"):
                by.setdefault(("competitor", str(raw.get("competitor"))), []).append((s.observed_at, s.value))
        elif s.kind == "visibility" and s.metric == "visibility":
            plat, persona = scope_of(s.source)
            if persona is not None:
                continue
            if platform:
                # platform-specific incident: other platforms (same cluster) are the controls
                if plat and plat != platform and s.prompt_cluster_id == incident.prompt_cluster_id:
                    by.setdefault(("platform", plat), []).append((s.observed_at, s.value))
            elif plat is None and s.prompt_cluster_id is not None and s.prompt_cluster_id != incident.prompt_cluster_id:
                by.setdefault(("cluster", str(s.prompt_cluster_id)), []).append((s.observed_at, s.value))
    for (group, name), pts in by.items():
        controls.append(ControlSeries(name=name, group=group, points=pts))
    return own_delta, controls


async def assess_incident_controls(session: Any, incident: Any) -> ControlAssessment:
    own, controls = await load_controls(session, incident)
    at = incident.first_observed_at or incident.detected_at
    return assess_control_movement(own, controls, at)
