"""Material query-fanout changes. Small wording drift is not an incident.

Rows come from Profound `POST /v2/reports/query-fanouts` after normalization. This module never calls the API
and never invents a query. A shift is evidence for an open incident, not a new alert by itself.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import utcnow
from app.domain.enums import EvidenceStatus, EvidenceType
from app.evidence.provenance import content_hash
from app.models.core import Incident, Organization, Signal
from app.models.evidence import Evidence

# Absolute share movement required before a query is worth showing. Shares <= 1 are fractions.
FRACTION_DELTA = 0.15
PERCENT_DELTA = 15.0


@dataclass(frozen=True)
class FanoutShift:
    kind: str  # competitor_emerged | share_shift
    query: str
    prompt: str | None
    before_share: float | None
    after_share: float
    competitor: str | None


def _threshold(shares: list[float]) -> float:
    return FRACTION_DELTA if shares and max(shares) <= 1.0 else PERCENT_DELTA


def _hit(query: str, competitors: list[str]) -> str | None:
    text = query.lower()
    for name in competitors:
        token = name.strip().lower()
        if len(token) < 3:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(token)}(?![a-z0-9])", text):
            return name
    return None


def material_fanout_shifts(rows: list[dict], *, competitors: list[str] | None = None) -> list[FanoutShift]:
    """Compare the latest date of each prompt+query with the previous dates.

    `rows` items: query, prompt, observed_at (datetime), share (float).
    """
    competitors = competitors or []
    grouped: dict[tuple[str, str], list[tuple[datetime, float]]] = {}
    shares: list[float] = []
    for row in rows:
        query = (row.get("query") or "").strip()
        share = row.get("share")
        at = row.get("observed_at")
        if not query or not isinstance(share, (int, float)) or not isinstance(at, datetime):
            continue
        grouped.setdefault((row.get("prompt") or "", query), []).append((at, float(share)))
        shares.append(float(share))
    need = _threshold(shares)
    out: list[FanoutShift] = []
    for (prompt, query), points in grouped.items():
        points.sort(key=lambda p: p[0])
        latest_at = points[-1][0]
        after = [v for at, v in points if at == latest_at]
        before = [v for at, v in points if at < latest_at]
        after_share = sum(after) / len(after)
        before_share = sum(before) / len(before) if before else None
        if before_share is None:
            if after_share < need:
                continue
            who = _hit(query, competitors)
            if who:
                out.append(FanoutShift("competitor_emerged", query, prompt or None, None, after_share, who))
            continue
        if abs(after_share - before_share) >= need:
            out.append(FanoutShift(
                "share_shift", query, prompt or None, before_share, after_share, _hit(query, competitors),
            ))
    out.sort(key=lambda s: abs((s.after_share or 0) - (s.before_share or 0)), reverse=True)
    return out


async def record_fanout_shifts(session: AsyncSession, incident_id: uuid.UUID) -> dict:
    """Attach material fanout shifts as evidence. No signals -> no rows. Idempotent for this incident."""
    inc = await session.get(Incident, incident_id)
    if inc is None:
        return {"fanout_shifts": 0, "reason": "incident_missing"}
    stmt = select(Signal).where(Signal.org_id == inc.org_id, Signal.kind == "query_fanout")
    if inc.prompt_cluster_id is not None:
        stmt = stmt.where(Signal.prompt_cluster_id == inc.prompt_cluster_id)
    signals = list((await session.execute(stmt)).scalars())
    if not signals:
        return {"fanout_shifts": 0, "reason": "no_fanout_signals"}
    org = await session.get(Organization, inc.org_id)
    names = []
    for domain in (org.competitor_domains or []) if org else []:
        label = str(domain).split(".")[0].strip()
        if label:
            names.append(label)
    rows = []
    for s in signals:
        raw = s.raw or {}
        query = (raw.get("row") or {}).get("query") or raw.get("query")
        rows.append({"query": query, "prompt": raw.get("prompt"), "observed_at": s.observed_at, "share": s.value})
    shifts = material_fanout_shifts(rows, competitors=names)
    await session.execute(delete(Evidence).where(
        Evidence.incident_id == inc.id, Evidence.retrieval_method == "profound.query_fanouts",
    ))
    now = utcnow()
    for shift in shifts:
        title = (
            f"Query fanout: {shift.competitor} appeared in \"{shift.query}\""
            if shift.kind == "competitor_emerged"
            else f"Query fanout share moved for \"{shift.query}\""
        )
        excerpt = (
            f"Share {shift.before_share if shift.before_share is not None else 'absent'} -> {shift.after_share}. "
            "Compared within this prompt cluster. Not a separate incident."
        )
        session.add(Evidence(
            incident_id=inc.id, type=EvidenceType.PROFOUND.value, status=EvidenceStatus.LIVE.value,
            title=title, source="profound", retrieval_method="profound.query_fanouts", retrieved_at=now,
            observed_at=inc.detected_at, excerpt=excerpt,
            content_hash=content_hash(f"{shift.kind}|{shift.query}|{shift.after_share}"),
            raw={"kind": "query_fanout_shift", "shift": shift.kind, "query": shift.query, "prompt": shift.prompt,
                 "before_share": shift.before_share, "after_share": shift.after_share,
                 "competitor": shift.competitor},
        ))
    await session.flush()
    return {"fanout_shifts": len(shifts), "considered": len(signals)}
