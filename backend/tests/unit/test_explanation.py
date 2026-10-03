"""The explanation must not present an unconstrained score as the chosen action."""

from app.incidents.explanation import build_explanation


def test_constraint_note_qualifies_unconstrained_scores():
    text = build_explanation(
        title="visibility moved",
        priority=40,
        metrics=[],
        hypotheses=[],
        hypotheses_meta={},
        selected_action="observe",
        policy_scores=[{"action": "create_comparison_content", "policy_score": 0.73, "selected": False}],
        verification_window=None,
        selection_note="root cause not confirmed by the evidence gate",
    )
    assert text.recommended_action == "observe"
    assert text.leading_hypothesis is None
    assert "unconstrained policy probabilities" in text.note
    assert "not confirmed" in text.note
    assert "caused" not in text.note
