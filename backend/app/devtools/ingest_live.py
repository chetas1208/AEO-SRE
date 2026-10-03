"""Run the same Profound ingestion path the worker uses (one org).

    cd backend && .venv/bin/python -m app.devtools.ingest_live [--org-id UUID]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from sqlalchemy import select

from app.connectors.profound.redact import redact_for_display
from app.core.db import get_sessionmaker
from app.models.core import Organization
from app.services.ingestion import run_scheduled_ingest


async def run(org_id: uuid.UUID | None) -> int:
    async with get_sessionmaker()() as session:
        if org_id is None:
            org_id = (await session.execute(select(Organization.id).limit(1))).scalar_one_or_none()
            if org_id is None:
                print("INGEST_LIVE_NO_ORG: create an organization first.", file=sys.stderr)
                return 2
        result = await run_scheduled_ingest(session, org_id)  # same service the worker job runs
        await session.commit()
    body = redact_for_display(result.to_dict())
    print(json.dumps(body, indent=2))
    if result.status in ("unavailable", "failed"):
        return 1
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Live Profound ingest for one organization.")
    p.add_argument("--org-id", type=uuid.UUID, default=None)
    args = p.parse_args()
    raise SystemExit(asyncio.run(run(args.org_id)))


if __name__ == "__main__":
    main()
