from __future__ import annotations

from typing import Any

from app.intelligence.laya.runtime import get_laya_runtime


def laya_capability_block() -> dict[str, Any]:
    rt = get_laya_runtime()
    if rt.ready:
        state = "READY"
    elif rt.loading:
        state = "LOADING"
    elif rt.enabled:
        state = "DEGRADED"
    else:
        state = "UNAVAILABLE"
    return {
        "state": state,
        "enabled": rt.enabled,
        "model_version": rt._model_version,
        "error": rt.error,
    }
