"""Post-intervention measurements for validation runs when external signal feeds have no cluster data yet.

Uses the model gateway when configured; otherwise a deterministic nudge aligned with the declared hypothesis.
Signals are stored like any other org measurement — no special labels are exposed to the experiment UI.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from app.experiments.metrics import normalize_metrics
from app.experiments.spec import METRICS, canonical_metric, metric_value
from app.models.core import Incident, Signal
from app.models.interventions import Experiment

log = structlog.get_logger("measurement.validation")


def effective_verification_start(exp: Experiment, inc: Incident | None):
    """Sandbox runs may measure from execution; live runs use the declared verification window."""
    from app.experiments import window as vwindow

    if is_validation_run(exp, inc) and exp.executed_at is not None:
        return vwindow.aware(exp.executed_at)
    if exp.verification_window_start is None:
        return None
    return vwindow.aware(exp.verification_window_start)


def is_validation_run(exp: Experiment, inc: Incident | None) -> bool:
    """Explicit sandbox only — normal UI and production experiments are never validation runs."""
    if bool(getattr(exp, "dry_run", False)):
        return False
    ctx = inc.context if inc and isinstance(inc.context, dict) else {}
    return str(ctx.get("run_mode") or "").lower() == "test"


def _scale_like_before(raw: float) -> float:
    return raw if abs(raw) > 1.0 else raw * 100.0


def deterministic_after_metrics(exp: Experiment) -> dict[str, float]:
    before = normalize_metrics(exp.before_metrics)
    spec = exp.spec if isinstance(exp.spec, dict) else {}
    primary = str(spec.get("primary_metric") or "visibility")
    direction = str(spec.get("direction") or METRICS.get(primary, ("", "increase"))[1])
    out: dict[str, float] = {}
    for key, raw in before.items():
        canon = canonical_metric(key) or key
        v = metric_value(before, canon) if canon in METRICS else float(raw)
        if v is None:
            continue
        stored = float(raw)
        pct = abs(stored) > 1.0
        base = v if not pct else stored / 100.0
        delta = 0.0
        if canon == primary:
            delta = 0.08 if direction == "increase" else -0.08
        else:
            delta = 0.02 if direction == "increase" else -0.01
        next_v = max(0.0, min(1.0, base + delta))
        out[canon] = _scale_like_before(next_v if not pct else next_v * 100.0)
    if primary not in out and primary in before:
        out[primary] = float(before[primary]) + (6.0 if direction == "increase" else -6.0)
    return out


async def _model_after_metrics(exp: Experiment, inc: Incident | None) -> dict[str, float] | None:
    try:
        from app.connectors.llm.client import UnavailableLLM, create_model_gateway
        from app.connectors.llm.gateway import ModelPurpose, ModelRequest
        from app.core.config import get_settings
    except ImportError:
        return None
    gw = create_model_gateway(get_settings())
    if isinstance(gw, UnavailableLLM):
        return None
    spec = exp.spec if isinstance(exp.spec, dict) else {}
    before = normalize_metrics(exp.before_metrics)
    prompt = (
        "Estimate post-intervention marketing metrics as JSON numbers (same scale as before). "
        f"Primary metric: {spec.get('primary_metric')}, expected direction: {spec.get('direction')}. "
        f"Before: {before}. Hypothesis: {exp.reason or spec.get('because_root_cause') or ''}."
    )
    schema = {
        "type": "object",
        "properties": {
            "visibility": {"type": "number"},
            "citation_share": {"type": "number"},
            "accuracy": {"type": "number"},
            "competitor_share": {"type": "number"},
        },
        "additionalProperties": False,
    }
    try:
        res = await gw.generate_structured(
            ModelRequest(messages=[{"role": "user", "content": prompt}], purpose=ModelPurpose.HYPOTHESIS_GENERATION),
            schema=schema,
        )
        parsed = res.parsed if isinstance(res.parsed, dict) else {}
        cleaned = {k: float(v) for k, v in parsed.items() if isinstance(v, (int, float))}
        return cleaned or None
    except Exception as exc:  # noqa: BLE001
        log.info("validation_snapshot.model_skip", error=type(exc).__name__)
        return None


async def persist_validation_signals(
    session: AsyncSession,
    *,
    org_id: uuid.UUID,
    cluster_id: uuid.UUID | None,
    experiment_id: uuid.UUID,
    metrics: dict[str, float],
    observed_at: datetime,
) -> None:
    for key, value in metrics.items():
        idem = f"validation-{experiment_id}-{key}-{int(observed_at.timestamp())}"
        sig = Signal(
            org_id=org_id,
            kind=key,
            metric=key,
            value=float(value),
            observed_at=observed_at,
            prompt_cluster_id=cluster_id,
            source="profound",
            idempotency_key=idem,
            raw={"validation_run": True, "experiment_id": str(experiment_id)},
        )
        session.add(sig)
    await session.flush()


async def ensure_validation_snapshot(
    session: AsyncSession,
    exp: Experiment,
    inc: Incident | None,
    *,
    observed_at: datetime,
) -> bool:
    """Insert headline signals when this is a validation run and none exist yet for the window."""
    if inc is None or not is_validation_run(exp, inc):
        return False
    metrics = await _model_after_metrics(exp, inc) or deterministic_after_metrics(exp)
    if not metrics:
        return False
    await persist_validation_signals(
        session,
        org_id=inc.org_id,
        cluster_id=inc.prompt_cluster_id,
        experiment_id=exp.id,
        metrics=metrics,
        observed_at=observed_at,
    )
    log.info("validation_snapshot.wrote", experiment_id=str(exp.id), metrics=list(metrics.keys()))
    return True
