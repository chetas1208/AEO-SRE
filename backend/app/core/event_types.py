"""Canonical SSE event types. Internal `stage` strings stay as they are (the UI humanizes them); every event also
carries `event_type`, one of a small stable vocabulary, so clients never parse stage names. Only real, persisted
pipeline events are mapped: nothing is synthesized, and an unknown stage keeps its own name."""
from __future__ import annotations

from typing import Any

PROVIDER_STEPS = {"evidence.profound": "profound", "evidence.answers": "profound", "evidence.fanout": "profound",
                  "evidence.web": "web", "evidence.collect": "web"}
CANONICAL = (
    "investigation.started", "investigation.completed", "provider.request.started", "provider.request.completed",
    "evidence.created", "hypothesis.proposed", "hypothesis.confirmed", "policy.completed", "approval.requested",
    "experiment.activated", "verification.scheduled", "verification.completed", "reward.created",
    "change_check.decided",  # Change Guard: a check touched this incident's experiment (small payload; see handoff-g1)
)
MAX_EVENT_METADATA_BYTES = 4096


def canonical_event_type(stage: str, status: str | None = None, metadata: dict[str, Any] | None = None) -> str:
    meta = metadata or {}
    if stage in ("investigation.started", "investigation.completed", "policy.completed"):
        return stage
    base, _, phase = stage.rpartition(".")
    if base in PROVIDER_STEPS and phase in ("started", "completed"):
        return f"provider.request.{phase}"
    if stage == "hypotheses.completed" and status != "failed":
        return "hypothesis.proposed"
    if stage == "gate.completed" and meta.get("confirmed") is True:
        return "hypothesis.confirmed"
    if stage in ("approval.pending", "approval_requested"):
        return "approval.requested"
    if stage in ("execution.package_ready", "execution.started"):
        return "experiment.activated"
    if stage == "verification.waiting":
        return "verification.scheduled"
    if stage == "verification.completed":
        return "verification.completed"
    if stage == "reward.completed":
        return "reward.created"
    return stage


def bounded_metadata(meta: dict[str, Any] | None) -> dict[str, Any]:
    """SSE payloads stay small: an oversized metadata blob is replaced by a marker (the full row stays in the DB and
    in `/events/history`)."""
    import json

    meta = meta or {}
    try:
        size = len(json.dumps(meta, default=str))
    except (TypeError, ValueError):
        return {"truncated": True}
    if size <= MAX_EVENT_METADATA_BYTES:
        return meta
    return {"truncated": True, "bytes": size, "keys": sorted(map(str, meta))[:20]}
