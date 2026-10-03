"""Stage-2 Profound answer rows. Scheduled ingest does not call this.

Citation details are requested only while an incident is being investigated. No key means no request
and no fabricated rows. The paginated answers endpoint is used; the SSE stream is not.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import utcnow
from app.domain.enums import EvidenceStatus, EvidenceType
from app.evidence.provenance import content_hash
from app.models.core import Incident
from app.models.evidence import Evidence

RETRIEVAL = "profound.answers"
_INCLUDE = [
    "run_id", "date", "model", "topic", "persona", "prompt", "prompt_id",
    "response", "mentions", "citations", "citation_details", "search_queries",
]


def _rows(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "rows", "answers"):
        value = payload.get(key)
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
    return []


def _category_id(payload: Any) -> str | None:
    rows = _rows(payload)
    if not rows and isinstance(payload, dict):
        rows = [r for r in (payload.get("categories") or []) if isinstance(r, dict)]
    for row in rows:
        cid = row.get("id") or row.get("category_id")
        if cid:
            return str(cid)
    return None


async def record_answer_details(session: AsyncSession, incident_id: uuid.UUID, *, client: Any = None) -> dict:
    settings = get_settings()
    if client is None and not settings.profound_api_key:
        return {"answer_rows": 0, "requests": 0, "reason": "profound_not_configured"}
    inc = await session.get(Incident, incident_id)
    if inc is None:
        return {"answer_rows": 0, "requests": 0, "reason": "incident_missing"}
    owns_client = client is None
    if client is None:
        from app.connectors.profound.client import ProfoundClient

        client = ProfoundClient()
    requests = 0
    try:
        categories = await client.list_categories()
        requests += 1
        category_id = _category_id(getattr(categories, "data", categories))
        if not category_id:
            return {"answer_rows": 0, "requests": requests, "reason": "no_category"}
        anchor = (inc.first_observed_at or inc.detected_at).date()
        from app.connectors.profound.requests import AnswersQuery

        query = AnswersQuery(
            category_id=category_id,
            start_date=(anchor - timedelta(days=7)).isoformat(),
            end_date=anchor.isoformat(),
            include=_INCLUDE,
            limit=min(settings.max_answer_rows, 200),
        )
        response = await client.answers(query)
        requests += 1
        rows = _rows(getattr(response, "data", response))[: settings.max_answer_rows]
    finally:
        if owns_client:
            await client.aclose()
    await session.execute(
        delete(Evidence).where(Evidence.incident_id == incident_id, Evidence.retrieval_method == RETRIEVAL)
    )
    now = utcnow()
    kept = 0
    for row in rows:
        text = str(row.get("response") or "")[:1500]
        if not text and not row.get("citations") and not row.get("citation_details"):
            continue
        excerpt = text or "citation detail without response text"
        session.add(Evidence(
            incident_id=incident_id,
            type=EvidenceType.PROFOUND.value,
            status=EvidenceStatus.LIVE.value,
            title=(str(row.get("prompt") or "Profound answer")[:512]),
            source="profound",
            excerpt=excerpt,
            retrieval_method=RETRIEVAL,
            retrieved_at=now,
            observed_at=inc.first_observed_at or inc.detected_at,
            content_hash=content_hash(excerpt),
            raw={
                "run_id": row.get("run_id"),
                "date": row.get("date"),
                "model": row.get("model"),
                "mentions": row.get("mentions") or [],
                "citations": row.get("citations") or [],
                "citation_details": row.get("citation_details") or [],
                "search_queries": row.get("search_queries") or [],
            },
        ))
        kept += 1
    await session.flush()
    return {"answer_rows": kept, "requests": requests, "reason": None, "citation_details": True}
