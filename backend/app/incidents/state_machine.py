"""Explicit, auditable incident state machine over ``IncidentState``.

Happy path (one step at a time, no skipping):

    detected -> triaged -> investigating -> evidence_ready -> root_cause_proposed
    -> root_cause_confirmed -> intervention_proposed -> awaiting_approval -> approved
    -> executing -> executed -> awaiting_verification -> verified -> rewarded -> closed

Loops: ``evidence_ready``/``root_cause_proposed`` may return to ``investigating`` (more evidence
needed); ``awaiting_approval`` may return to ``intervention_proposed`` (rejected / modified).
Low-confidence incidents may go ``root_cause_proposed -> intervention_proposed`` only with an
``observe`` action (``metadata={"action": "observe"}``); nothing external is mutated by observe.
``dismissed`` and ``failed`` are reachable from every non-terminal state. ``closed``, ``dismissed``
and ``failed`` are terminal.

Verification cannot be skipped: no edge leads from approved/executing/executed straight to
verified, rewarded or closed. ``root_cause_confirmed`` can additionally be tied to the evidence
gate by passing ``gate=<GateResult>``; an unconfirmed gate result rejects the transition.

Pure functions over any object exposing a mutable ``state`` attribute; no DB access. Callers
persist the returned audit record (e.g. via ``app.core.audit.audit``).
"""
from __future__ import annotations

from collections import deque
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any, Protocol

from app.domain.enums import ActionType
from app.domain.enums import IncidentState as S

TERMINAL_STATES: frozenset[S] = frozenset({S.CLOSED, S.DISMISSED, S.FAILED})

_FORWARD: dict[S, set[S]] = {
    S.DETECTED: {S.TRIAGED},
    S.TRIAGED: {S.INVESTIGATING},
    S.INVESTIGATING: {S.EVIDENCE_READY},
    S.EVIDENCE_READY: {S.ROOT_CAUSE_PROPOSED, S.INVESTIGATING},
    S.ROOT_CAUSE_PROPOSED: {S.ROOT_CAUSE_CONFIRMED, S.INVESTIGATING, S.INTERVENTION_PROPOSED},
    S.ROOT_CAUSE_CONFIRMED: {S.INTERVENTION_PROPOSED},
    S.INTERVENTION_PROPOSED: {S.AWAITING_APPROVAL},
    S.AWAITING_APPROVAL: {S.APPROVED, S.INTERVENTION_PROPOSED},
    S.APPROVED: {S.EXECUTING},
    S.EXECUTING: {S.EXECUTED},
    S.EXECUTED: {S.AWAITING_VERIFICATION},
    S.AWAITING_VERIFICATION: {S.VERIFIED},
    S.VERIFIED: {S.REWARDED, S.CLOSED},
    S.REWARDED: {S.CLOSED},
    S.CLOSED: set(),
    S.DISMISSED: set(),
    S.FAILED: set(),
}

ALLOWED: dict[S, set[S]] = {
    src: set(dsts) | (set() if src in TERMINAL_STATES else {S.DISMISSED, S.FAILED})
    for src, dsts in _FORWARD.items()
}

# Edges that are only legal when the caller supplies matching metadata.
_OBSERVE_ONLY_EDGES: frozenset[tuple[S, S]] = frozenset({(S.ROOT_CAUSE_PROPOSED, S.INTERVENTION_PROPOSED)})


class IllegalTransition(ValueError):
    def __init__(self, src: S | str, dst: S | str, message: str | None = None) -> None:
        self.src = src
        self.dst = dst
        super().__init__(message or f"illegal incident transition {_name(src)} -> {_name(dst)}")


class _GateLike(Protocol):
    confirmed: bool


def _name(s: Any) -> str:
    return s.value if isinstance(s, S) else str(s)


def _coerce(state: Any) -> S:
    if isinstance(state, S):
        return state
    try:
        return S(str(getattr(state, "value", state)))
    except ValueError as exc:
        raise IllegalTransition(state, state, f"unknown incident state {state!r}") from exc


def is_terminal(state: S | str) -> bool:
    return _coerce(state) in TERMINAL_STATES


def next_states(src: S | str) -> set[S]:
    return set(ALLOWED[_coerce(src)])


def can_transition(src: S | str, dst: S | str) -> bool:
    try:
        s, d = _coerce(src), _coerce(dst)
    except IllegalTransition:
        return False
    return d in ALLOWED[s]


def shortest_path(src: S | str, dst: S | str) -> list[S] | None:
    """Shortest unguarded route src -> dst (no dismissed/failed or observe-only edges); None if unreachable."""
    s, d = _coerce(src), _coerce(dst)
    if s == d:
        return [s]
    seen, queue = {s}, deque([[s]])
    while queue:
        path = queue.popleft()
        for nxt in sorted(_FORWARD[path[-1]], key=lambda x: x.value):
            if nxt in seen or (path[-1], nxt) in _OBSERVE_ONLY_EDGES:
                continue
            if nxt == d:
                return [*path, nxt]
            seen.add(nxt)
            queue.append([*path, nxt])
    return None


def transition(
    incident: Any,
    dst: S | str,
    actor: str,
    reason: str,
    *,
    metadata: Mapping[str, Any] | None = None,
    gate: _GateLike | None = None,
    on_audit: Callable[[dict[str, Any]], None] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Move ``incident.state`` to ``dst`` or raise ``IllegalTransition``. Returns the audit record.

    ``actor`` and ``reason`` are mandatory so every transition is attributable. The incident is
    mutated only after every check passed.
    """
    src = _coerce(incident.state)
    try:
        target = _coerce(dst)
    except IllegalTransition:
        raise IllegalTransition(src, dst, f"unknown target state {dst!r}") from None
    if not isinstance(actor, str) or not actor.strip():
        raise IllegalTransition(src, target, "transition requires a non-empty actor")
    if not isinstance(reason, str) or not reason.strip():
        raise IllegalTransition(src, target, "transition requires a non-empty reason")
    if src in TERMINAL_STATES:
        raise IllegalTransition(src, target, f"{src.value} is terminal; no further transitions")
    if target not in ALLOWED[src]:
        raise IllegalTransition(src, target)

    meta = dict(metadata or {})
    if (src, target) in _OBSERVE_ONLY_EDGES and str(meta.get("action")) != ActionType.OBSERVE.value:
        raise IllegalTransition(
            src, target,
            "an unconfirmed root cause may only advance with an observe action "
            "(metadata={'action': 'observe'}); confirm the root cause via the evidence gate otherwise",
        )
    if target == S.ROOT_CAUSE_CONFIRMED and (gate is None or not bool(gate.confirmed)):
        raise IllegalTransition(src, target, "evidence gate has not confirmed the root cause")
    if target == S.ROOT_CAUSE_CONFIRMED and gate is not None:
        meta.setdefault("gate_confirmed", True)

    incident.state = target
    record: dict[str, Any] = {
        "incident_id": str(getattr(incident, "id", None)) if getattr(incident, "id", None) is not None else None,
        "entity_type": "incident",
        "event": "state_transition",
        "from_state": src.value,
        "to_state": target.value,
        "actor": actor.strip(),
        "reason": reason.strip(),
        "metadata": meta,
        "at": (now or datetime.now(UTC)).isoformat(),
    }
    if on_audit is not None:
        on_audit(record)
    return record
