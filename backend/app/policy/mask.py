"""Action eligibility mask, applied BEFORE scoring.

A masked action is never scored and never selectable. The taxonomy (`ActionType`) is unchanged; the mask only
removes actions that cannot be executed meaningfully for THIS incident, using facts read from persisted state
(see `app.policy.context`). Rules fail closed: an unknown fact is "not eligible", `observe` is always eligible.

| action                  | eligible iff                                                         |
|-------------------------|----------------------------------------------------------------------|
| observe                 | always                                                               |
| update_existing_page    | a relevant owned page exists (owned evidence with a URL, not failed)  |
| publisher_outreach      | a third-party (external) source was cited / collected as evidence      |
| create_canonical_page   | canonical content gap: NO relevant owned page covers the topic         |
| create_faq, structured_data, create_comparison_content | always (no extra precondition)        |
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.domain.enums import ActionType


@dataclass(frozen=True)
class EligibilityFacts:
    has_owned_page: bool = False
    has_third_party_source: bool = False
    # canonical gap = no owned page covers the topic. Only claimed when evidence collection actually produced rows
    # (no evidence at all is "unknown", which is NOT a gap).
    evidence_collected: bool = False

    @property
    def canonical_gap(self) -> bool:
        return self.evidence_collected and not self.has_owned_page

    def to_json(self) -> dict[str, bool]:
        return {"has_owned_page": self.has_owned_page, "has_third_party_source": self.has_third_party_source,
                "evidence_collected": self.evidence_collected, "canonical_gap": self.canonical_gap}


@dataclass(frozen=True)
class ActionMask:
    eligible: tuple[ActionType, ...]
    masked: dict[str, str] = field(default_factory=dict)  # action value -> reason

    def to_json(self) -> dict[str, object]:
        return {"eligible": [a.value for a in self.eligible], "masked": dict(self.masked)}


def compute_action_mask(facts: EligibilityFacts) -> ActionMask:
    masked: dict[str, str] = {}
    if not facts.has_owned_page:
        masked[ActionType.UPDATE_EXISTING_PAGE.value] = "no relevant owned page exists to update"
    if not facts.has_third_party_source:
        masked[ActionType.PUBLISHER_OUTREACH.value] = "no third-party source was cited or collected"
    if not facts.canonical_gap:
        masked[ActionType.CREATE_CANONICAL_PAGE.value] = (
            "an owned page already covers the topic" if facts.has_owned_page
            else "no canonical content gap established (evidence not collected)")
    eligible = tuple(a for a in ActionType if a.value not in masked)
    return ActionMask(eligible, masked)
