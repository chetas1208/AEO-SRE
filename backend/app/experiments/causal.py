"""Separate a measured before/after change from a causal claim.

A positive reward with a recorded confounder is a favorable association at low causal confidence.
This function never returns high confidence.
"""
from __future__ import annotations


def qualify_association(reward_total: float, confounders: list[str] | None = None) -> dict[str, object]:
    names = [c.strip() for c in (confounders or []) if isinstance(c, str) and c.strip()]
    if reward_total > 0.05:
        direction = "favorable"
    elif reward_total < -0.05:
        direction = "unfavorable"
    else:
        direction = "neutral"
    if names:
        confidence = "low"
        extra = " Recorded confounders: " + "; ".join(names) + "."
    elif direction == "neutral":
        confidence = "low"
        extra = " The change is inside the neutral band (±0.05 reward)."
    else:
        confidence = "medium"
        extra = " No confounder was recorded."
    statement = (
        f"Outcome direction {direction}. Causal confidence {confidence}.{extra} "
        "This is a before/after association, not a demonstrated cause."
    )
    return {
        "direction": direction,
        "causal_confidence": confidence,
        "confounders": names,
        "statement": statement,
    }
