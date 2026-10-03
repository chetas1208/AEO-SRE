"""Worker-side Mixpanel ingestion (LIVE + BACKFILL)."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.integrations.mixpanel.client import MixpanelClient, MixpanelError
from app.integrations.mixpanel.correlate import correlate_event
from app.integrations.mixpanel.cursor import upsert_cursor
from app.integrations.mixpanel.mapper import normalize_row
from app.integrations.mixpanel.schemas import IngestBatchResult
from app.models.core import Organization, Setting
from app.models.telemetry import LiveTelemetryEvent

log = structlog.get_logger()
CATALOG_KEY = "mixpanel.event_catalog"


async def resolve_org_id(session: AsyncSession, settings: Settings) -> uuid.UUID | None:
    raw = (settings.mixpanel_default_org_id or "").strip()
    if raw:
        try:
            oid = uuid.UUID(raw)
            if await session.get(Organization, oid):
                return oid
        except ValueError:
            pass
    domain = (settings.mixpanel_org_domain or "mixpanel.com").strip().lower()
    row = (await session.execute(select(Organization.id).where(Organization.domain == domain))).first()
    return row[0] if row else None


async def _persist_catalog(session: AsyncSession, names: list[str]) -> None:
    existing = await session.get(Setting, CATALOG_KEY)
    payload = {"event_names": sorted(set(names)), "updated_at": datetime.now(UTC).isoformat()}
    if existing:
        existing.value = payload
    else:
        session.add(Setting(key=CATALOG_KEY, value=payload))


async def ingest_mixpanel(
    session: AsyncSession,
    *,
    org_id: uuid.UUID | None = None,
    mode: str = "LIVE",
    backfill_days: int | None = None,
) -> IngestBatchResult:
    settings = get_settings()
    client = MixpanelClient(settings)
    if not client.configured:
        return IngestBatchResult(
            mode="LIVE" if mode == "LIVE" else "BACKFILL",
            window_from=datetime.now(UTC),
            window_to=datetime.now(UTC),
            status="not_configured",
        )

    oid = org_id or await resolve_org_id(session, settings)
    t0 = time.perf_counter()
    now = datetime.now(UTC)
    if mode == "BACKFILL":
        days = backfill_days or settings.mixpanel_backfill_days
        window_from = now - timedelta(days=days)
        from_d, to_d = window_from.date(), now.date()
    else:
        window_from = now - timedelta(seconds=settings.mixpanel_poll_seconds + 30)
        from_d, to_d = window_from.date(), now.date()

    inserted = duplicates = unresolved = correlated = fetched = 0
    last_event_id: str | None = None
    last_ts = window_from
    try:
        names = await client.event_names(from_d, to_d)
        if names:
            await _persist_catalog(session, names)
        async for raw in client.export_events(from_d, to_d):
            fetched += 1
            norm = normalize_row(raw, org_id=oid, received_at=now)
            if norm.tenant_resolution == "UNRESOLVED":
                unresolved += 1
            norm = await correlate_event(session, norm)
            if norm.correlation_method != "UNRESOLVED":
                correlated += 1
            exp_id = None
            if norm.experiment_id:
                try:
                    exp_id = uuid.UUID(norm.experiment_id)
                except ValueError:
                    exp_id = None
            values = dict(
                source="MIXPANEL",
                source_event=norm.source_event,
                source_event_id=norm.source_event_id,
                occurred_at=norm.occurred_at,
                received_at=norm.received_at,
                organization_id=oid,
                tenant_resolution=norm.tenant_resolution,
                campaign_id=norm.campaign_id,
                agent_id=norm.agent_id,
                experiment_id=exp_id,
                product_id=norm.product_id,
                properties=norm.properties,
                correlation_keys=norm.correlation_keys,
                raw_hash=norm.raw_hash,
                correlation_method=norm.correlation_method,
                correlation_confidence=norm.correlation_confidence,
            )
            if session.bind and session.bind.dialect.name == "postgresql":
                stmt = pg_insert(LiveTelemetryEvent).values(**values).on_conflict_do_nothing(
                    index_elements=["source", "source_event_id"]
                )
                res = await session.execute(stmt)
                if res.rowcount:
                    inserted += 1
                else:
                    duplicates += 1
            else:
                exists = (
                    await session.execute(
                        select(LiveTelemetryEvent.id).where(
                            LiveTelemetryEvent.source == "MIXPANEL",
                            LiveTelemetryEvent.source_event_id == norm.source_event_id,
                        ).limit(1)
                    )
                ).first()
                if exists:
                    duplicates += 1
                else:
                    session.add(LiveTelemetryEvent(**values))
                    inserted += 1
            last_event_id = norm.source_event_id
            if norm.occurred_at > last_ts:
                last_ts = norm.occurred_at
    except MixpanelError as exc:
        log.warning("mixpanel.ingest_failed", error=str(exc), status=exc.status)
        return IngestBatchResult(
            mode="LIVE" if mode == "LIVE" else "BACKFILL",
            window_from=window_from,
            window_to=now,
            fetched=fetched,
            inserted=inserted,
            duplicates=duplicates,
            unresolved_tenant=unresolved,
            correlated=correlated,
            latency_ms=round((time.perf_counter() - t0) * 1000, 1),
            status=f"error:{exc.status or 'unknown'}",
        )

    if oid and (inserted or fetched):
        await upsert_cursor(
            session,
            oid,
            last_successful_timestamp=last_ts,
            last_event_id=last_event_id,
            last_query_window={"from": from_d.isoformat(), "to": to_d.isoformat(), "mode": mode},
        )
    log.info(
        "mixpanel.batch_ingested",
        fetched=fetched,
        inserted=inserted,
        duplicates=duplicates,
        org_id=str(oid) if oid else None,
    )
    return IngestBatchResult(
        mode="LIVE" if mode == "LIVE" else "BACKFILL",
        window_from=window_from,
        window_to=now,
        fetched=fetched,
        inserted=inserted,
        duplicates=duplicates,
        unresolved_tenant=unresolved,
        correlated=correlated,
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        status="ok",
    )


async def data_quality(session: AsyncSession, org_id: uuid.UUID | None) -> dict:
    filters = [LiveTelemetryEvent.organization_id == org_id] if org_id else []
    total = (
        await session.execute(select(func.count()).select_from(LiveTelemetryEvent).where(*filters))
    ).scalar_one()
    unresolved = (
        await session.execute(
            select(func.count()).select_from(LiveTelemetryEvent).where(
                LiveTelemetryEvent.tenant_resolution == "UNRESOLVED", *filters
            )
        )
    ).scalar_one()
    mapped = (
        await session.execute(
            select(func.count()).select_from(LiveTelemetryEvent).where(
                LiveTelemetryEvent.correlation_method != "UNRESOLVED", *filters
            )
        )
    ).scalar_one()
    pct = round(100.0 * mapped / total, 1) if total else 0.0
    return {"total": total, "mapped_pct": pct, "unresolved_tenant": unresolved}
