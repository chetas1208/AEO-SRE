"""Unit tests for app.incidents.state_machine. Pure; uses a plain stand-in object (no DB)."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import pairwise
from types import SimpleNamespace

import pytest
from app.domain.enums import IncidentState as S
from app.incidents.state_machine import (
    ALLOWED,
    TERMINAL_STATES,
    IllegalTransition,
    can_transition,
    is_terminal,
    next_states,
    shortest_path,
    transition,
)

HAPPY_PATH = [
    S.DETECTED, S.TRIAGED, S.INVESTIGATING, S.EVIDENCE_READY, S.ROOT_CAUSE_PROPOSED,
    S.ROOT_CAUSE_CONFIRMED, S.INTERVENTION_PROPOSED, S.AWAITING_APPROVAL, S.APPROVED, S.EXECUTING,
    S.EXECUTED, S.AWAITING_VERIFICATION, S.VERIFIED, S.REWARDED, S.CLOSED,
]


@dataclass
class FakeIncident:
    """Test double; stands in for app.models.core.Incident (duck-typed)."""
    state: S | str = S.DETECTED
    id: str = "inc-1"


@dataclass
class FakeGate:
    confirmed: bool


def test_allowed_covers_every_state():
    assert set(ALLOWED) == set(S)


def test_full_happy_path_walks_and_audits_each_step():
    inc = FakeIncident()
    records = []
    for src, dst in pairwise(HAPPY_PATH):
        gate = SimpleNamespace(confirmed=True) if dst == S.ROOT_CAUSE_CONFIRMED else None
        rec = transition(inc, dst, "system:pipeline", f"{src.value} done", on_audit=records.append, gate=gate)
        assert inc.state == dst
        assert rec["from_state"] == src.value and rec["to_state"] == dst.value
    assert len(records) == len(HAPPY_PATH) - 1
    assert inc.state == S.CLOSED


def test_audit_record_shape():
    inc = FakeIncident()
    at = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    rec = transition(inc, S.TRIAGED, " alice ", " score above threshold ", metadata={"priority": 71.2}, now=at)
    assert rec == {
        "incident_id": "inc-1", "entity_type": "incident", "event": "state_transition",
        "from_state": "detected", "to_state": "triaged", "actor": "alice",
        "reason": "score above threshold", "metadata": {"priority": 71.2}, "at": at.isoformat(),
    }


@pytest.mark.parametrize(
    ("src", "dst"),
    [
        (S.DETECTED, S.EXECUTED),
        (S.DETECTED, S.APPROVED),
        (S.DETECTED, S.INVESTIGATING),
        (S.TRIAGED, S.ROOT_CAUSE_CONFIRMED),
        (S.INVESTIGATING, S.ROOT_CAUSE_CONFIRMED),
        (S.EVIDENCE_READY, S.ROOT_CAUSE_CONFIRMED),
        (S.ROOT_CAUSE_PROPOSED, S.APPROVED),
        (S.INTERVENTION_PROPOSED, S.APPROVED),
        (S.INTERVENTION_PROPOSED, S.EXECUTING),
        (S.AWAITING_APPROVAL, S.EXECUTING),
        (S.APPROVED, S.EXECUTED),
        (S.VERIFIED, S.EXECUTING),
        (S.CLOSED, S.DETECTED),
    ],
)
def test_illegal_jumps_rejected_and_state_unchanged(src, dst):
    inc = FakeIncident(state=src)
    assert not can_transition(src, dst)
    with pytest.raises(IllegalTransition):
        transition(inc, dst, "system", "try")
    assert inc.state == src


def test_verification_cannot_be_skipped():
    for src in (S.APPROVED, S.EXECUTING, S.EXECUTED):
        for dst in (S.VERIFIED, S.REWARDED, S.CLOSED):
            assert not can_transition(src, dst), (src, dst)
    assert next_states(S.EXECUTED) - {S.DISMISSED, S.FAILED} == {S.AWAITING_VERIFICATION}
    assert next_states(S.AWAITING_VERIFICATION) - {S.DISMISSED, S.FAILED} == {S.VERIFIED}
    # no path from executed to rewarded that avoids verified
    path = shortest_path(S.EXECUTED, S.REWARDED)
    assert path is not None and S.AWAITING_VERIFICATION in path and S.VERIFIED in path


def test_reward_requires_verified():
    assert not can_transition(S.AWAITING_VERIFICATION, S.REWARDED)
    assert can_transition(S.VERIFIED, S.REWARDED)


def test_detected_to_executed_rejected_and_unique_path_through_gates():
    path = shortest_path(S.DETECTED, S.EXECUTED)
    assert path is not None
    for gate_state in (S.EVIDENCE_READY, S.ROOT_CAUSE_CONFIRMED, S.AWAITING_APPROVAL, S.APPROVED):
        assert gate_state in path


@pytest.mark.parametrize("terminal", [S.DISMISSED, S.FAILED])
@pytest.mark.parametrize("src", [s for s in S if s not in TERMINAL_STATES])
def test_dismiss_and_fail_from_any_non_terminal(src, terminal):
    inc = FakeIncident(state=src)
    rec = transition(inc, terminal, "alice", "not actionable")
    assert inc.state == terminal and rec["from_state"] == src.value


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATES, key=lambda s: s.value))
def test_terminal_states_are_final(terminal):
    assert is_terminal(terminal)
    assert next_states(terminal) == set()
    for dst in S:
        inc = FakeIncident(state=terminal)
        with pytest.raises(IllegalTransition, match="terminal"):
            transition(inc, dst, "system", "reopen")


def test_self_transition_rejected():
    inc = FakeIncident(state=S.INVESTIGATING)
    with pytest.raises(IllegalTransition):
        transition(inc, S.INVESTIGATING, "system", "noop")


@pytest.mark.parametrize(("actor", "reason"), [("", "why"), ("   ", "why"), ("bob", ""), ("bob", "  ")])
def test_actor_and_reason_required(actor, reason):
    inc = FakeIncident()
    with pytest.raises(IllegalTransition):
        transition(inc, S.TRIAGED, actor, reason)
    assert inc.state == S.DETECTED


def test_loops_back_for_more_evidence_and_re_proposal():
    inc = FakeIncident(state=S.EVIDENCE_READY)
    transition(inc, S.INVESTIGATING, "system", "need more evidence")
    assert inc.state == S.INVESTIGATING
    inc = FakeIncident(state=S.AWAITING_APPROVAL)
    transition(inc, S.INTERVENTION_PROPOSED, "alice", "rejected; re-propose")
    assert inc.state == S.INTERVENTION_PROPOSED


def test_unconfirmed_root_cause_only_advances_with_observe():
    inc = FakeIncident(state=S.ROOT_CAUSE_PROPOSED)
    with pytest.raises(IllegalTransition, match="observe"):
        transition(inc, S.INTERVENTION_PROPOSED, "system", "low confidence")
    with pytest.raises(IllegalTransition, match="observe"):
        transition(inc, S.INTERVENTION_PROPOSED, "system", "x", metadata={"action": "update_existing_page"})
    rec = transition(inc, S.INTERVENTION_PROPOSED, "system", "low confidence", metadata={"action": "observe"})
    assert inc.state == S.INTERVENTION_PROPOSED and rec["metadata"] == {"action": "observe"}


def test_confirmed_requires_gate_when_gate_supplied():
    inc = FakeIncident(state=S.ROOT_CAUSE_PROPOSED)
    with pytest.raises(IllegalTransition, match="evidence gate"):
        transition(inc, S.ROOT_CAUSE_CONFIRMED, "system", "gate", gate=FakeGate(False))
    assert inc.state == S.ROOT_CAUSE_PROPOSED
    rec = transition(inc, S.ROOT_CAUSE_CONFIRMED, "system", "gate passed", gate=FakeGate(True))
    assert inc.state == S.ROOT_CAUSE_CONFIRMED and rec["metadata"]["gate_confirmed"] is True


def test_string_states_are_coerced():
    inc = FakeIncident(state="detected")
    transition(inc, "triaged", "system", "go")
    assert inc.state is S.TRIAGED
    assert can_transition("triaged", "investigating")
    assert not can_transition("triaged", "nonsense")
    assert not can_transition("bogus", "triaged")


def test_unknown_states_raise():
    with pytest.raises(IllegalTransition):
        transition(FakeIncident(state="bogus"), S.TRIAGED, "system", "x")
    with pytest.raises(IllegalTransition):
        transition(FakeIncident(), "bogus", "system", "x")


def test_allowed_mapping_is_not_mutated_by_next_states():
    next_states(S.DETECTED).add(S.EXECUTED)
    assert S.EXECUTED not in ALLOWED[S.DETECTED]


def test_shortest_path_basic():
    assert shortest_path(S.DETECTED, S.TRIAGED) == [S.DETECTED, S.TRIAGED]
    assert shortest_path(S.CLOSED, S.DETECTED) is None
    assert shortest_path(S.VERIFIED, S.VERIFIED) == [S.VERIFIED]
    assert shortest_path(S.DETECTED, S.CLOSED) == [s for s in HAPPY_PATH if s != S.REWARDED]
    assert shortest_path(S.DETECTED, S.REWARDED) == HAPPY_PATH[:-1]


def test_on_audit_not_called_when_illegal():
    calls = []
    with pytest.raises(IllegalTransition):
        transition(FakeIncident(), S.EXECUTED, "system", "x", on_audit=calls.append)
    assert calls == []
