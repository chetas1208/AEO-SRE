"""Optional AI-perception protocol. The discovery-gap module (or anything else) may implement it later.

Until a provider is registered, `check_intent` reports `ai_perception: "unavailable"`. Perception is never fabricated.
"""
from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol, runtime_checkable

PerceptionState = Literal["supports", "contradicts", "silent"]


@dataclass
class PerceptionVerdict:
    """What AI engines say about one statement. `silent` = no observation either way."""

    state: PerceptionState
    evidence: list[str] = field(default_factory=list)  # short perceived claims / engine names, no personal data
    source_mode: str | None = None  # LIVE | SIMULATED | ... as reported by the provider


@runtime_checkable
class PerceptionProvider(Protocol):
    async def perceive(self, org_id: uuid.UUID, statements: Sequence[str]) -> dict[str, PerceptionVerdict]:
        """Verdict per statement (keyed by the statement text). Raise to signal the provider is unavailable."""


_provider: PerceptionProvider | None = None


def set_perception_provider(provider: PerceptionProvider | None) -> None:
    global _provider
    _provider = provider


def get_perception_provider() -> PerceptionProvider | None:
    return _provider
