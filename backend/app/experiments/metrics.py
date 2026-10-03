"""Small helpers for metric snapshots and JSON-safety (import-light)."""
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any


def jsonable(value: Any) -> Any:
    """Deep-copy into plain JSON types (numpy arrays, enums, datetimes handled)."""
    if hasattr(value, "tolist"):
        value = value.tolist()
    return json.loads(json.dumps(value, default=lambda o: o.tolist() if hasattr(o, "tolist") else str(o)))


def normalize_metrics(metrics: Any) -> dict[str, float]:
    """Accept {name: value} or [{metric|name: ..., value|current: ...}] -> flat numeric dict."""
    out: dict[str, float] = {}
    items: list[tuple[Any, Any]]
    if isinstance(metrics, Mapping):
        items = list(metrics.items())
    elif isinstance(metrics, list):
        items = []
        for m in metrics:
            if isinstance(m, Mapping):
                name = m.get("metric") or m.get("name")
                val = m.get("value", m.get("current"))
                if name is not None:
                    items.append((name, val))
    else:
        return out
    for k, v in items:
        if isinstance(v, bool) or v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isfinite(f):
            out[str(k)] = f
    return out


def as_fraction(v: float) -> float:
    """Same convention as app.learning.reward: |v| > 1 is a 0-100 percentage."""
    return v / 100.0 if abs(v) > 1.0 else v
