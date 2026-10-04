"""Live Profound overlays for marketing campaigns (read-only from ingested Signal rows)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.core import Signal
from app.services.org_defaults import resolve_live_brand_org_id
from app.services.pipeline import _canon, metric_snapshot


async def resolve_brand_org_id(session: AsyncSession) -> uuid.UUID | None:
    return await resolve_live_brand_org_id(session)


def _pp_delta(now: float | None, before: float | None) -> float | None:
    if now is None or before is None:
        return None
    # Profound visibility may be 0–1 or 0–100; normalize to percentage points for display.
    if now <= 1.5 and before <= 1.5:
        return round((now - before) * 100.0, 2)
    return round(now - before, 2)


def _effectiveness(visibility_delta_pp: float | None, citation_delta_pp: float | None) -> str:
    if visibility_delta_pp is None and citation_delta_pp is None:
        return "NO_DATA"
    v = visibility_delta_pp or 0.0
    c = citation_delta_pp or 0.0
    if v >= 2.0 or c >= 1.0:
        return "WORKING"
    if v <= -5.0 or c <= -3.0:
        return "AT_RISK"
    if v < 0 or c < 0:
        return "WATCH"
    return "STABLE"


async def _earliest_metric_snapshot(
    session: AsyncSession,
    org_id: uuid.UUID,
    *,
    start: datetime | None,
    end: datetime | None,
) -> dict[str, Any]:
    """First observed value per canonical metric in the window (for in-period trend when prior week is empty)."""
    stmt = (
        select(Signal)
        .where(Signal.org_id == org_id)
        .order_by(Signal.observed_at.asc())
    )
    if start is not None:
        stmt = stmt.where(Signal.observed_at >= start)
    if end is not None:
        stmt = stmt.where(Signal.observed_at <= end)
    rows = (await session.execute(stmt)).scalars().all()
    earliest: dict[str, tuple[datetime, float]] = {}
    for s in rows:
        key = _canon(s.metric, s.kind)
        if key is None:
            continue
        if key not in earliest:
            earliest[key] = (s.observed_at, float(s.value))
    return {k: v[1] for k, v in earliest.items()}


async def live_profound_overlay(session: AsyncSession, *, org_id: uuid.UUID | None = None) -> dict[str, Any]:
    """Snapshot + 7d delta from Postgres signals (Profound-ingested). No fabricated numbers."""
    oid = org_id or await resolve_brand_org_id(session)
    if oid is None:
        return {"status": "NO_ORG", "source": "PROFOUND", "source_mode": "LIVE"}

    now = datetime.now(UTC)
    week_ago = now - timedelta(days=7)
    current = await metric_snapshot(session, oid, None, start=week_ago, end=now)
    prior_end = week_ago
    prior_start = week_ago - timedelta(days=7)
    prior = await metric_snapshot(session, oid, None, start=prior_start, end=prior_end)

    cur_v = current.get("visibility")
    cur_c = current.get("citation_share")
    prev_v = prior.get("visibility")
    prev_c = prior.get("citation_share")

    delta_basis = "week_over_week"
    if prev_v is None and prev_c is None and (cur_v is not None or cur_c is not None):
        early = await _earliest_metric_snapshot(session, oid, start=week_ago, end=now)
        prev_v = early.get("visibility", prev_v)
        prev_c = early.get("citation_share", prev_c)
        if prev_v is not None or prev_c is not None:
            delta_basis = "in_period_7d"

    vis_delta = _pp_delta(
        float(cur_v) if cur_v is not None else None,
        float(prev_v) if prev_v is not None else None,
    )
    cit_delta = _pp_delta(
        float(cur_c) if cur_c is not None else None,
        float(prev_c) if prev_c is not None else None,
    )

    signal_count = (
        await session.execute(
            select(func.count()).select_from(Signal).where(Signal.org_id == oid, Signal.source == "profound")
        )
    ).scalar_one()

    last_ingest = (
        await session.execute(select(func.max(Signal.observed_at)).where(Signal.org_id == oid))
    ).scalar()

    effectiveness = _effectiveness(vis_delta, cit_delta)

    return {
        "status": "OK" if signal_count else "NO_SIGNALS",
        "source": "PROFOUND",
        "source_mode": "LIVE",
        "organization_id": str(oid),
        "signal_count": signal_count,
        "last_signal_at": last_ingest.isoformat() if last_ingest else None,
        "metrics": {
            "visibility": cur_v,
            "citation_share": cur_c,
            "accuracy": current.get("accuracy"),
            "competitor_share": current.get("competitor_share"),
        },
        "delta_7d_pp": {
            "visibility": vis_delta,
            "citation_share": cit_delta,
        },
        "delta_basis": delta_basis,
        "campaign_effectiveness": effectiveness,
        "attribution_note": (
            (
                "7-day Profound delta (week-over-week)."
                if delta_basis == "week_over_week"
                else "7-day Profound delta (first vs latest observation in window — prior week had no signals)."
            )
            + " Observational; not causal to any single campaign."
            if signal_count
            else "Run Profound ingest for this org — no LIVE signals in Postgres yet."
        ),
    }


def merge_profound_impact(campaign: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Attach live overlay; refresh profound_impact display fields when LIVE data exists."""
    out = {**campaign, "profound_live": overlay}
    if overlay.get("status") not in ("OK",):
        return out
    delta = overlay.get("delta_7d_pp") or {}
    metrics = overlay.get("metrics") or {}
    legacy = dict(campaign.get("profound_impact") or {})
    if metrics.get("visibility") is not None:
        legacy["live_visibility"] = metrics.get("visibility")
        legacy["prompt_coverage_pct"] = round(float(metrics["visibility"]) * 100.0, 1) if float(metrics["visibility"]) <= 1.5 else round(float(metrics["visibility"]), 1)
    if metrics.get("citation_share") is not None:
        legacy["live_citation_share"] = metrics.get("citation_share")
    if delta.get("visibility") is not None:
        legacy["visibility_shift_pp"] = delta["visibility"]
    if delta.get("citation_share") is not None:
        legacy["citation_share_shift_pp"] = delta["citation_share"]
    legacy["ai_perception_status"] = overlay.get("campaign_effectiveness", "STABLE")
    legacy["attribution_note"] = overlay["attribution_note"]
    legacy["data_source"] = "LIVE_PROFOUND"
    out["profound_impact"] = legacy
    out["campaign_effectiveness"] = overlay.get("campaign_effectiveness")
    return out
