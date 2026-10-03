"""Known small graph for exact feature assertions (seeded into Aura under a unique temporary organization_id).

Time A = 2026-10-03T12:00:00Z. See test_live_queries.py for the hand-computed expectations.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

from app.graph.schema import NODE_IDS, merge_node

A = datetime(2026, 10, 3, 12, 0, 0, tzinfo=UTC)


def ts(delta: timedelta) -> str:
    return (A + delta).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def new_org() -> str:
    return f"n3test-{uuid.uuid4().hex[:12]}"


class Seeder:
    def __init__(self, org: str, prefix: str):
        self.org, self.p = org, prefix
        self.nodes: list[tuple[str, dict]] = []
        self.rels: list[tuple[str, str, str, str, str]] = []

    def id(self, name: str) -> str:
        return f"{self.p}-{name}"

    def node(self, label: str, name: str, **props) -> str:
        nid = self.id(name)
        self.nodes.append((label, {NODE_IDS[label]: nid, **props}))
        return nid

    def rel(self, l1: str, n1: str, typ: str, l2: str, n2: str) -> None:
        self.rels.append((l1, self.id(n1), typ, l2, self.id(n2)))

    def statements(self) -> list[tuple[str, dict]]:
        out = []
        for label, props in self.nodes:
            nid = props.pop(NODE_IDS[label])
            out.append(merge_node(label, self.org, nid, props))
        for l1, a, typ, l2, b in self.rels:
            q = (f"MATCH (a:{l1} {{{NODE_IDS[l1]}: $a, organization_id: $organization_id}}), "
                 f"(b:{l2} {{{NODE_IDS[l2]}: $b, organization_id: $organization_id}}) MERGE (a)-[:{typ}]->(b)")
            out.append((q, {"a": a, "b": b, "organization_id": self.org}))
        return out


def seed_main(org: str) -> Seeder:
    s = Seeder(org, org)
    H, M, D = (lambda n: timedelta(hours=n)), (lambda n: timedelta(minutes=n)), (lambda n: timedelta(days=n))
    s.node("Agent", "a1", agent_type="seo")
    s.node("Agent", "a2", agent_type="content")
    s.node("Target", "t1", target_key="/pricing", target_type="page", protected=False)
    s.node("Target", "t2", target_key="/enterprise/security", target_type="page", protected=True)
    s.node("Target", "t3", target_key="/blog", target_type="page", protected=False)
    s.node("Experiment", "e1", number=12, status="running", started_at=ts(-H(1)), ends_at=ts(timedelta(hours=8, minutes=29)))
    s.node("Experiment", "e2", number=13, status="done", started_at=ts(-H(30)), ends_at=ts(-H(2)))
    s.rel("Experiment", "e1", "MEASURES", "Target", "t2")
    s.rel("Experiment", "e2", "MEASURES", "Target", "t3")
    s.node("PromptCluster", "pc1")
    s.node("Claim", "c1", claim_type="pricing", text="Pricing is $10")

    def change(name, agent, targets, at, action="edit_copy", status="EXECUTED", **kw):
        s.node("ChangeSet", name, occurred_at=ts(at), action_type=action, status=status, **kw)
        s.rel("Agent", agent, "PROPOSED", "ChangeSet", name)
        for t in targets:
            s.rel("ChangeSet", name, "MODIFIES", "Target", t)

    change("cs_old", "a1", ["t1", "t2"], -D(3))
    change("cs2", "a2", ["t1"], -M(30))
    change("cs3", "a1", ["t1"], -M(10), status="PENDING")
    change("cs_focal", "a1", ["t1"], timedelta(0), status="PENDING")
    change("cs_future", "a2", ["t1"], H(1))
    change("cs_other", "a2", ["t3"], -H(2), action="publish")
    change("cs_del", "a1", ["t2"], -M(5), status="DELAYED", number=31)
    for c in ("cs_old", "cs2", "cs3", "cs_focal", "cs_future"):
        s.rel("ChangeSet", c, "ABOUT", "PromptCluster", "pc1")
    for c in ("cs_old", "cs2", "cs3", "cs_focal"):
        s.rel("ChangeSet", c, "ALTERS", "Claim", "c1")
    s.rel("ChangeSet", "cs_focal", "DERIVED_FROM", "ChangeSet", "cs3")
    s.rel("ChangeSet", "cs3", "DERIVED_FROM", "ChangeSet", "cs_old")

    def conflict(name, typ, at, involves, **kw):
        s.node("Conflict", name, conflict_type=typ, occurred_at=ts(at), **kw)
        for c in involves:
            s.rel("Conflict", name, "INVOLVES", "ChangeSet", c)

    conflict("k1", "DUPLICATE", -M(1), ["cs_focal", "cs2"])
    s.rel("Conflict", "k1", "AFFECTS", "Target", "t1")
    conflict("k2", "CANONICAL_CONTRADICTION", -M(1), ["cs_focal"])
    s.rel("Conflict", "k2", "ABOUT", "Claim", "c1")
    conflict("k3", "DUPLICATE", -M(20), ["cs3", "cs2"])
    conflict("k4", "CANONICAL_CONTRADICTION", -D(2), ["cs_old"])
    s.rel("Conflict", "k4", "ABOUT", "Claim", "c1")
    conflict("k_future", "DUPLICATE", H(1), ["cs_focal", "cs_future"])
    conflict("kd", "PROTECTED_TARGET", -M(4), ["cs_del"], summary="protected target under measurement")
    s.rel("Conflict", "kd", "INVOLVES", "Experiment", "e1")

    def decision(name, cs, dec, at):
        s.node("Decision", name, decision=dec, occurred_at=ts(at))
        s.rel("Decision", name, "DECIDES_ON", "ChangeSet", cs)

    decision("d_old", "cs_old", "ALLOW", -D(3))
    decision("d2", "cs2", "DELAY", -M(29))
    decision("d3", "cs3", "BLOCK", -M(9))
    decision("d_other", "cs_other", "ALLOW", -H(2))
    decision("d_future", "cs_future", "BLOCK", H(1))
    decision("dd", "cs_del", "DELAY", -M(4))
    s.rel("Decision", "dd", "BASED_ON", "Conflict", "kd")
    # lineage chain for cs_del
    s.node("AgentRun", "r1", occurred_at=ts(-M(6)))
    s.node("Event", "ev1", event_type="CHANGE_PROPOSED", occurred_at=ts(-M(5)))
    s.rel("AgentRun", "r1", "EMITTED", "Event", "ev1")
    s.rel("Event", "ev1", "ASSOCIATED_WITH", "ChangeSet", "cs_del")
    s.node("Approval", "ap", occurred_at=ts(-M(2)))
    s.rel("Approval", "ap", "APPROVES", "Decision", "dd")
    s.node("Observation", "ob", occurred_at=ts(-M(1)))
    s.node("Outcome", "out", occurred_at=ts(-M(1)), outcome_label="valid", reward=1.0)
    s.rel("Decision", "dd", "PRODUCED", "Observation", "ob")
    s.rel("Observation", "ob", "PRODUCED", "Outcome", "out")
    return s


def seed_other_org(org: str) -> Seeder:
    """A second organization with the SAME target_key / agent_type: nothing of it may leak into org A."""
    s = Seeder(org, org)
    s.node("Agent", "b1", agent_type="seo")
    s.node("Target", "tb", target_key="/pricing", target_type="page", protected=True)
    s.node("ChangeSet", "csb", occurred_at=ts(-timedelta(minutes=2)), action_type="edit_copy", status="PENDING")
    s.rel("Agent", "b1", "PROPOSED", "ChangeSet", "csb")
    s.rel("ChangeSet", "csb", "MODIFIES", "Target", "tb")
    s.node("Conflict", "kb", conflict_type="DUPLICATE", occurred_at=ts(-timedelta(minutes=1)))
    s.rel("Conflict", "kb", "INVOLVES", "ChangeSet", "csb")
    s.rel("Conflict", "kb", "AFFECTS", "Target", "tb")
    s.node("Decision", "db", decision="BLOCK", occurred_at=ts(-timedelta(minutes=1)))
    s.rel("Decision", "db", "DECIDES_ON", "ChangeSet", "csb")
    return s


def chain(org: str, n: int = 9) -> Seeder:
    s = Seeder(org, org)
    for i in range(n):
        s.node("ChangeSet", f"ch{i}", occurred_at=ts(-timedelta(hours=i + 1)), status="PENDING")
    for i in range(n - 1):
        s.rel("ChangeSet", f"ch{i}", "DERIVED_FROM", "ChangeSet", f"ch{i + 1}")
    return s
