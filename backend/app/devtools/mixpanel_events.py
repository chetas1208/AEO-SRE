"""Discover Mixpanel event names (redacted). python -m app.devtools.mixpanel_events"""

from __future__ import annotations

import asyncio
import json
from collections import Counter
from datetime import date, timedelta

from app.integrations.mixpanel.client import MixpanelClient


async def main_async() -> None:
    client = MixpanelClient()
    if not client.configured:
        print(json.dumps({"status": "MIXPANEL LIVE BLOCKED", "reason": "not configured"}))
        return
    end = date.today()
    start = end - timedelta(days=7)
    names = await client.event_names(start, end)
    sample = await client.sample_events(limit=50)
    counts = Counter(str(r.get("event")) for r in sample if r.get("event"))
    props: Counter[str] = Counter()
    for row in sample[:20]:
        for k in (row.get("properties") or {}):
            if not str(k).startswith("$"):
                props[k] += 1
    print(
        json.dumps(
            {
                "status": "ok",
                "project_id": client.project_id,
                "time_range": {"from": start.isoformat(), "to": end.isoformat()},
                "event_names": names[:200],
                "sample_counts": dict(counts.most_common(30)),
                "top_properties": [k for k, _ in props.most_common(40)],
            },
            indent=2,
        )
    )


def main() -> None:
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
