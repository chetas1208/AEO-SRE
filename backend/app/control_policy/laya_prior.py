"""Build Laya state + control-action prior for Change Guard checks."""
from __future__ import annotations

from typing import Any

from app.changeguard.decision import Decision
from app.intelligence.laya.runtime import get_laya_runtime
from app.intelligence.laya.schemas import LayaChoiceResult, LayaState


def _finding_types(findings: list[Any]) -> list[str]:
    out: list[str] = []
    for f in findings:
        t = f.get("type") if isinstance(f, dict) else getattr(f, "type", None)
        if t:
            out.append(str(t))
    return out


def build_laya_state(
    *,
    baseline: Decision,
    action_type: str,
    source_mode: str,
    claims_count: int,
    risk: str | None,
    finding_types: list[str],
) -> LayaState:
    return LayaState(
        values={
            "baseline_decision": baseline.value,
            "action_type": action_type,
            "source_mode": source_mode,
            "claims_count": claims_count,
            "risk": (risk or "").lower(),
            "finding_count": len(finding_types),
            "finding_types": sorted(finding_types),
        }
    )


def laya_control_prior(
    *,
    baseline: Decision,
    action_type: str,
    source_mode: str,
    claims_count: int,
    risk: str | None,
    findings: list[Any],
) -> LayaChoiceResult:
    state = build_laya_state(
        baseline=baseline,
        action_type=action_type,
        source_mode=source_mode,
        claims_count=claims_count,
        risk=risk,
        finding_types=_finding_types(findings),
    )
    return get_laya_runtime().control_action_prior(state)
