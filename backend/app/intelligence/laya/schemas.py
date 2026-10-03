"""Typed bounded decisions for Laya (choice / score / noul). Not chat."""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class LayaState(BaseModel):
    """Compact operational state passed into a Laya task (JSON-serializable)."""

    values: dict[str, Any] = Field(default_factory=dict)


class LayaChoiceResult(BaseModel):
    """One forward-pass style outcome for a discrete choice task."""

    available: bool = False
    model_version: str | None = None
    question: str = ""
    options: list[str] = Field(default_factory=list)
    selected: str | None = None
    distribution: dict[str, float] = Field(default_factory=dict)
    confidence: float = 0.0  # max prob or calibrated score
    escalation_probability: float = 1.0  # 1.0 => should escalate when unavailable/low confidence
    source: str = "unavailable"  # unavailable | heuristic | checkpoint
    detail: str | None = None
