"""Task templates for control-plane Laya calls (typed decisions only)."""

CONTROL_ACTION_QUESTION = "What control action should be considered for this change check?"


def control_action_options() -> list[str]:
    return ["ALLOW", "DELAY", "REQUIRE_REVIEW", "BLOCK", "MERGE"]
