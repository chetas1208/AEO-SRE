"""Run the discovery-gap check for one organization against the REAL Profound API.

    cd backend && .venv/bin/python -m app.devtools.discovery_gap_run --org <uuid> [--dry-run] [--category <id>]

--dry-run fetches and analyzes but persists nothing. Without --dry-run incidents/evidence are written (idempotent).
Exit codes: 0 ok, 1 skipped (honest no-op: no canonical truth / FactCheck unavailable / ...), 2 failed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid

from app.connectors.profound.redact import redact_for_display
from app.core.db import get_sessionmaker
from app.discovery_gap.service import run_discovery_gap


async def run(org: uuid.UUID, dry: bool, category: str | None) -> int:
    async with get_sessionmaker()() as session:
        rep = await run_discovery_gap(session, org, dry_run=dry, category_id=category)
        if dry:
            await session.rollback()
        else:
            await session.commit()
    print(json.dumps(redact_for_display(rep.to_dict()), indent=2, default=str))
    return 0 if rep.status == "ok" else (2 if rep.status == "failed" else 1)


def main() -> None:
    p = argparse.ArgumentParser(description="Discovery-gap check (canonical truth vs AI-perceived claims).")
    p.add_argument("--org", type=uuid.UUID, required=True)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--category", default=None, help="Profound category id (default: cached/domain-matched)")
    a = p.parse_args()
    try:
        raise SystemExit(asyncio.run(run(a.org, a.dry_run, a.category)))
    except ValueError as exc:
        print(f"DISCOVERY_GAP_ERROR: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main()
