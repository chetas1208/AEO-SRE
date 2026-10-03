"""Outcome methodology: measured before/after -> FAVORABLE | UNFAVORABLE | NEUTRAL | INCONCLUSIVE (+ reward to learn from).

Deterministic arithmetic only; no model is involved. Documented in docs/notes/handoff-b4.md.

Label (from the PRE-DECLARED primary metric, see `spec.py`)
    gain = (after - before) in the declared direction, in fractions (0.02 = 2 percentage points)
    gain >=  NOISE_BAND  -> FAVORABLE      gain <= -NOISE_BAND -> UNFAVORABLE      otherwise -> NEUTRAL
    INCONCLUSIVE when the primary metric is missing before/after, when the reward is not computable, or when a HARD
    confounder makes the movement unattributable (another intervention overlapped; the move is explained by a
    platform-wide shift; an OBSERVE baseline was disturbed by an intervention).
Reward (fed to the bandit) = `compute_reward` over the metrics actually available; components are persisted separately
    (visibility, citation, accuracy, competitive, action_cost, risk_penalty; metrics not measured are NOT stored as 0).
    INCONCLUSIVE -> NO policy update (a reward of 0 would be a made-up label). Failed execution -> no outcome at all.
OBSERVE (deliberate no-remediation monitoring experiment) additionally gets an observe label:
    self_recovery (gain >= band) -> FAVORABLE   persistent (|gain| < band) -> NEUTRAL   worsened (gain <= -band) -> UNFAVORABLE
    Rewards of OBSERVE credit "the problem moved this way while we did nothing"; they are the policy's no-action
    baseline for similar contexts, NOT a counterfactual control for the other actions.
Causal language: the outcome is an association. Causal confidence is `low` (confounders / neutral) or `medium`
    (measured change, no detected confounder); it is never `high`; nothing here claims formal causality.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.domain.enums import ActionType, Risk
from app.experiments.causal import qualify_association
from app.experiments.spec import METRICS, choose_primary, metric_value
from app.learning.reward import RewardResult, compute_reward

NOISE_BAND = 0.02  # 2 percentage points on a 0..1 metric: below this a change is not distinguished from noise
PLATFORM_EXPLAINS_FRACTION = 0.8  # platform-wide movement >= 80% of our move (same sign) => unattributable
METHOD_VERSION = "outcome-v1"


class Outcome(StrEnum):
    FAVORABLE = "favorable"
    UNFAVORABLE = "unfavorable"
    NEUTRAL = "neutral"
    INCONCLUSIVE = "inconclusive"


class ObserveOutcome(StrEnum):
    SELF_RECOVERY = "self_recovery"
    PERSISTENT = "persistent"
    WORSENED = "worsened"
    INCONCLUSIVE = "inconclusive"


@dataclass
class OutcomeAssessment:
    outcome: Outcome
    observe_outcome: ObserveOutcome | None
    reward: RewardResult | None
    learn: bool
    confounders: list[dict[str, Any]] = field(default_factory=list)
    causal_confidence: str = "low"
    causal_statement: str = ""
    primary_metric: str | None = None
    primary_gain: float | None = None
    methodology: dict[str, Any] = field(default_factory=dict)

    def components(self) -> dict[str, float]:
        """Reward components actually measured (+ cost and risk). Missing metrics are omitted, not zero-filled."""
        if self.reward is None:
            return {}
        missing = set(self.reward.missing)
        return {k: float(v) for k, v in self.reward.components.items() if k not in missing}


def _gain(before: float, after: float, direction: str) -> float:
    d = after - before
    return d if direction == "increase" else -d


def assess(
    *, before: Mapping[str, Any], after: Mapping[str, Any], action: ActionType | str, risk: Risk | str,
    spec: Mapping[str, Any] | None, confounders: list[dict[str, Any]] | None = None, late: bool = False,
    weights: Mapping[str, float] | None = None, band: float = NOISE_BAND,
) -> OutcomeAssessment:
    action, risk = ActionType(action), Risk(risk)
    confounders = list(confounders or [])
    observe = action is ActionType.OBSERVE
    spec_declared = bool(spec)
    primary = (spec or {}).get("primary_metric") or choose_primary(None, before)
    method = {"version": METHOD_VERSION, "noise_band": band, "spec_declared": spec_declared,
              "primary_metric": primary, "late_measurement": late, "observe": observe}

    def inconclusive(why: str, reward: RewardResult | None = None, gain: float | None = None) -> OutcomeAssessment:
        method["reason"] = why
        return OutcomeAssessment(
            Outcome.INCONCLUSIVE, ObserveOutcome.INCONCLUSIVE if observe else None, reward, False, confounders, "low",
            f"Outcome inconclusive ({why}). No policy update. This is not evidence for or against the action.",
            primary, gain, method)

    # No comparable metric at all -> NoObservation propagates: nothing was measured yet, so the caller keeps waiting
    # (no outcome row). Only a measurement that exists but cannot judge the declared primary metric is INCONCLUSIVE.
    reward = compute_reward(before, after, action, risk, weights)
    if primary not in METRICS:
        return inconclusive("no measurable primary metric", reward)
    direction = (spec or {}).get("direction") or METRICS[primary][1]
    b, a = metric_value(before, primary), metric_value(after, primary)
    if b is None or a is None:
        return inconclusive("primary metric unavailable before or after", reward)
    gain = _gain(b, a, direction)
    method.update({"direction": direction, "before": b, "after": a, "gain": gain})
    method["reward_missing_metrics"] = list(reward.missing)
    hard = [c for c in confounders if c.get("hard")]
    if hard:
        return inconclusive("unattributable: " + ", ".join(sorted({c["kind"] for c in hard})), reward, gain)

    label = Outcome.FAVORABLE if gain >= band else Outcome.UNFAVORABLE if gain <= -band else Outcome.NEUTRAL
    obs_label = None
    if observe:
        obs_label = {Outcome.FAVORABLE: ObserveOutcome.SELF_RECOVERY, Outcome.NEUTRAL: ObserveOutcome.PERSISTENT,
                     Outcome.UNFAVORABLE: ObserveOutcome.WORSENED}[label]
    qual = qualify_association(reward.total, [str(c["kind"]) for c in confounders])
    method["causal"] = qual
    return OutcomeAssessment(label, obs_label, reward, True, confounders, qual["causal_confidence"],
                             qual["statement"], primary, gain, method)


# ---------------------------------------------------------------------------------------------------------
# confounders (detected from persisted data between execution and measurement; absence of data = not detected)


def _series_move(rows: list[Any], t0: datetime, t1: datetime) -> float | None:
    """Last value at/before t1 minus last value at/before t0 for one signal series (fractions)."""
    from app.experiments.window import aware

    pts = sorted(((aware(r.observed_at), float(r.value)) for r in rows), key=lambda p: p[0])
    first = [v for t, v in pts if t <= aware(t0)]
    last = [v for t, v in pts if t <= aware(t1)]
    if not first or not last:
        return None
    f, g = first[-1], last[-1]
    f = f / 100.0 if abs(f) > 1.0 else f
    g = g / 100.0 if abs(g) > 1.0 else g
    return g - f


async def detect_confounders(
    session: AsyncSession, experiment: Any, *, before: Mapping[str, Any], after: Mapping[str, Any],
    observed_at: datetime, primary: str | None, band: float = NOISE_BAND,
) -> list[dict[str, Any]]:
    """Confounders visible in persisted data. Each: {"kind", "detail", "hard"}. Hard ones make the outcome INCONCLUSIVE."""
    from app.domain.enums import EvidenceType
    from app.experiments.collision import overlapping_interventions
    from app.experiments.window import aware
    from app.models.core import Incident, PromptCluster, Signal
    from app.models.evidence import Evidence

    out: list[dict[str, Any]] = []
    if experiment.executed_at is None:
        return out
    t0, t1 = aware(experiment.executed_at), aware(observed_at)
    observe = ActionType(experiment.selected_action) is ActionType.OBSERVE
    inc = await session.get(Incident, experiment.incident_id)

    # overlapping real interventions on the same target / prompt-cluster scope
    others = await overlapping_interventions(session, experiment, t1)
    if others:
        out.append({"kind": "intervention_during_observe" if observe else "overlapping_intervention",
                    "detail": f"{len(others)} other intervention(s) active on the same scope: "
                              + ", ".join(str(o.id) for o in others[:5]), "hard": True})

    # competitor change (measured competitor share moved materially)
    if primary != "competitor_share":
        cb, ca = metric_value(before, "competitor_share"), metric_value(after, "competitor_share")
        if cb is not None and ca is not None and abs(ca - cb) >= band:
            out.append({"kind": "competitor_change",
                        "detail": f"competitor share moved {ca - cb:+.3f}", "hard": False})

    # platform-wide movement: the same metric moved the same way in the org's OTHER clusters
    if primary and inc is not None and primary in METRICS:
        from app.services.pipeline import _canon

        sigs = list((await session.execute(select(Signal).where(Signal.org_id == inc.org_id,
                                                                  Signal.observed_at <= t1))).scalars())
        by_cluster: dict[Any, list[Any]] = {}
        for s in sigs:
            if _canon(s.metric, s.kind) == primary and s.prompt_cluster_id != inc.prompt_cluster_id:
                by_cluster.setdefault(s.prompt_cluster_id, []).append(s)
        moves = [m for rows in by_cluster.values() if (m := _series_move(rows, t0, t1)) is not None]
        own_b, own_a = metric_value(before, primary), metric_value(after, primary)
        if moves and own_b is not None and own_a is not None:
            moves.sort()
            med = moves[len(moves) // 2]
            own = own_a - own_b
            same = med * own > 0 and abs(med) >= band
            if same:
                explains = min(abs(med) / abs(own), 1.0) if own else 1.0
                out.append({"kind": "platform_wide_movement", "hard": explains >= PLATFORM_EXPLAINS_FRACTION,
                            "detail": f"{len(moves)} other cluster(s) moved a median {med:+.3f} "
                                      f"(explains {explains:.0%} of {own:+.3f})"})

    # new prompt in the affected cluster
    if inc is not None and inc.prompt_cluster_id:
        cl = await session.get(PromptCluster, inc.prompt_cluster_id)
        if cl is not None and cl.updated_at and aware(cl.updated_at) > t0 and aware(cl.updated_at) <= t1:
            out.append({"kind": "new_prompt", "detail": "the prompt cluster changed after execution", "hard": False})

    # brand content changed elsewhere / new citation source after execution
    ev = list((await session.execute(select(Evidence).where(Evidence.incident_id == experiment.incident_id))).scalars())
    target = str((experiment.proposed_change or {}).get("target_url") or "").rstrip("/").lower()
    for e in ev:
        stamp = e.retrieved_at or e.created_at
        if stamp is None or not (t0 < aware(stamp) <= t1):
            continue
        if e.type == EvidenceType.OWNED.value and e.status == "changed" and (e.url or "").rstrip("/").lower() != target:
            out.append({"kind": "brand_content_changed", "detail": f"owned page changed: {e.url}", "hard": False})
        elif e.type in (EvidenceType.EXTERNAL.value, EvidenceType.COMPETITOR.value):
            out.append({"kind": "new_citation_source", "detail": f"new {e.type} source after execution: {e.url}",
                        "hard": False})
    return out
