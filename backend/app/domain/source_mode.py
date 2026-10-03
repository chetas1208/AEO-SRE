"""Provenance for ingested signals: distinguish live Profound from fixture/replay/test.

The mode is PERSISTED at ingest time (Signal.raw["source_mode"], ingestion-run records) and read back from there.
`source_mode_for_signal` only falls back to the legacy `source` label for rows written before the mode was
persisted. It never looks at an organization name or domain.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any


class SignalSourceMode(StrEnum):
    LIVE = "LIVE"
    FIXTURE = "FIXTURE"
    REPLAY = "REPLAY"
    TEST = "TEST"


def _label_mode(source: str) -> SignalSourceMode:
    s = (source or "").strip().lower()
    if s == "dev_fixture" or s.startswith("dev_fixture"):
        return SignalSourceMode.FIXTURE
    if s == "historical_replay" or s.startswith("historical_replay"):
        return SignalSourceMode.REPLAY
    if s in ("test", "pytest", "factory") or s.startswith("test_"):
        return SignalSourceMode.TEST
    return SignalSourceMode.LIVE


def source_mode_for_signal(source: str, raw: Mapping[str, Any] | None = None) -> SignalSourceMode:
    """Fail closed: a signal is LIVE only if EVERY piece of evidence says live. A persisted mode that claims LIVE can
    never launder a row whose source label or `_fixture` marker says otherwise."""
    label = _label_mode(source)
    persisted: SignalSourceMode | None = None
    mode = (raw or {}).get("source_mode")
    if mode:
        try:
            persisted = SignalSourceMode(str(mode).upper())
        except ValueError:
            persisted = None
    if raw and raw.get("_fixture"):
        return SignalSourceMode.FIXTURE
    if persisted is not None and persisted is not SignalSourceMode.LIVE:
        return persisted
    return label
