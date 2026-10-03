"""Safety action mask derived ONLY from deterministic Change Guard rule findings (never from a learner).

    invalid action digest                    -> {BLOCK}
    canonical hard contradiction (BLOCK)     -> {BLOCK, REQUIRE_REVIEW}
    protected-target collision (DELAY)       -> {DELAY, REQUIRE_REVIEW}
    otherwise                                -> all five decisions

Precedence when several fire: invalid digest > canonical contradiction > protected collision (BLOCK > DELAY in the
guard's own precedence, and {BLOCK, REVIEW} is the stricter set). A masked action is never scored or selectable;
the learner chooses only among eligible actions. The baseline decision is always inside the mask (asserted by tests).
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.changeguard.decision import ACTIVE_EXPERIMENT, CANONICAL_CONFLICT, Decision

ALL_ACTIONS: tuple[Decision, ...] = (Decision.ALLOW, Decision.MERGE, Decision.DELAY, Decision.REQUIRE_REVIEW,
                                     Decision.BLOCK)

RULE_INVALID_DIGEST = "invalid_digest"
RULE_CANONICAL = "canonical_hard_contradiction"
RULE_PROTECTED = "protected_target_collision"


@dataclass(frozen=True)
class SafetyMask:
    eligible: tuple[Decision, ...]
    masked: dict[str, str] = field(default_factory=dict)  # action -> rule that removed it
    rules: tuple[str, ...] = ()

    def allows(self, action: Decision | str) -> bool:
        return Decision(action) in self.eligible

    def to_json(self) -> dict[str, Any]:
        return {"eligible": [a.value for a in self.eligible], "masked": dict(self.masked), "rules": list(self.rules)}


def _get(f: Any, key: str) -> Any:
    return f.get(key) if isinstance(f, Mapping) else getattr(f, key, None)


def compute_mask(findings: Iterable[Any], *, digest_valid: bool = True) -> SafetyMask:
    fs = list(findings)
    canonical = any(_get(f, "type") == CANONICAL_CONFLICT and Decision(_get(f, "decision")) == Decision.BLOCK
                    for f in fs)
    protected = any(_get(f, "type") == ACTIVE_EXPERIMENT and Decision(_get(f, "decision")) == Decision.DELAY
                    for f in fs)
    if not digest_valid:
        allowed, rule = {Decision.BLOCK}, RULE_INVALID_DIGEST
        rules: tuple[str, ...] = (RULE_INVALID_DIGEST,)
    elif canonical:
        allowed, rule = {Decision.BLOCK, Decision.REQUIRE_REVIEW}, RULE_CANONICAL
        rules = (RULE_CANONICAL,) + ((RULE_PROTECTED,) if protected else ())
    elif protected:
        allowed, rule = {Decision.DELAY, Decision.REQUIRE_REVIEW}, RULE_PROTECTED
        rules = (RULE_PROTECTED,)
    else:
        return SafetyMask(ALL_ACTIONS)
    eligible = tuple(a for a in ALL_ACTIONS if a in allowed)
    masked = {a.value: rule for a in ALL_ACTIONS if a not in allowed}
    return SafetyMask(eligible, masked, rules)
