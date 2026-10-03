"""Test support: an in-memory graph that mirrors the projector's semantics, and a scriptable fake Neo4j client.

`MemGraph.apply(rows)` implements the documented plan semantics in Python (MERGE on business id, state guard on
`state_at`, endpoint stubs `projected=false`, ABOUT / EMITTED edges for events). It lets Postgres-backed tests compare
incremental projection with replay without a database server; the Cypher itself is exercised by tests/graph_projection/test_live.py.
"""
from __future__ import annotations

import re
from typing import Any

from app.graph import model as gm
from app.graph.errors import GraphQueryError, GraphUnavailable
from app.graph.names import APP_NAMESPACE


class MemGraph:
    def __init__(self) -> None:
        self.nodes: dict[tuple[str, str], dict[str, Any]] = {}
        self.rels: set[tuple[str, str, str, str, str]] = set()

    def _stub(self, label: str, nid: str, org: str) -> dict[str, Any]:
        n = self.nodes.get((label, nid))
        if n is None:
            n = self.nodes[(label, nid)] = {"organization_id": gm.node_org(label, org), "app": APP_NAMESPACE,
                                            "projected": False}
        return n

    def apply(self, rows) -> None:
        for r in rows:
            plan, org = r.payload, str(r.organization_id)
            for n in plan.get("nodes") or []:
                node = self._stub(n["label"], n["id"], org)
                if not n.get("touch"):
                    node.update(n.get("props") or {})
                    node["projected"] = True
                if n.get("state") and n.get("state_at"):
                    if node.get("state_at") is None or node["state_at"] <= n["state_at"]:
                        node.update(n["state"])
                        node["state_at"] = n["state_at"]
            for rel in plan.get("rels") or []:
                (l1, a), (l2, b) = rel["from"], rel["to"]
                self._stub(l1, a, org)
                self._stub(l2, b, org)
                self.rels.add((l1, a, rel["type"], l2, b))
            ev = plan.get("event")
            if ev:
                eid = str(r.id)
                node = self._stub("Event", eid, org)
                node.update({**(ev.get("props") or {}), "event_type": ev["type"], "occurred_at": ev["occurred_at"],
                             "recorded_at": ev["recorded_at"], "source": ev["source"], "source_mode": ev["source_mode"],
                             "correlation_id": ev.get("correlation_id"), "aggregate_type": r.aggregate_type,
                             "aggregate_id": r.aggregate_id, "projected": True})
                for lab, nid in ev.get("about") or []:
                    self._stub(lab, nid, org)
                    self.rels.add(("Event", eid, "ABOUT", lab, nid))
                if ev.get("emitted_by"):
                    lab, nid = ev["emitted_by"]
                    self._stub(lab, nid, org)
                    self.rels.add((lab, nid, "EMITTED", "Event", eid))

    def snapshot(self) -> tuple[dict, frozenset]:
        return ({k: dict(sorted(v.items())) for k, v in self.nodes.items()}, frozenset(self.rels))

    def audit_rows(self, org: str) -> list[dict[str, Any]]:
        return [{"label": lab, "id": nid, "projected": n["projected"]} for (lab, nid), n in self.nodes.items()
                if n["organization_id"] == org]


class FakeClient:
    """Scriptable stand-in for GraphClient (projector / replay / audit only call these members)."""

    configured = True
    database = "testdb"

    def __init__(self, graph: MemGraph | None = None) -> None:
        self.graph = graph
        self.batches: list[list[tuple[str, dict]]] = []
        self.writes: list[tuple[str, dict]] = []
        self.reads: list[tuple[str, dict]] = []
        self.outage: Exception | None = None
        self.poison_ids: set[str] = set()
        self._n2_schema_ok = True

    async def run_write_batch(self, statements, *, timeout=None):
        stmts = [(q, dict(p)) for q, p in statements]
        if self.outage:
            raise self.outage
        if self.poison_ids:
            for _q, p in stmts:
                if any(str(row.get("id")) in self.poison_ids or str(row.get("a")) in self.poison_ids
                       for row in p.get("rows", [])):
                    raise GraphQueryError("poison statement", code="Neo.ClientError.Statement.SyntaxError")
        self.batches.append(stmts)
        return [[] for _ in stmts]

    async def run_write(self, query, params=None, *, timeout=None):
        if self.outage:
            raise self.outage
        self.writes.append((query, dict(params or {})))
        return [{"c": 0}]

    async def run_read(self, query, params=None, *, timeout=None):
        self.reads.append((query, dict(params or {})))
        if self.outage:
            raise self.outage
        if self.graph is not None and "DISTINCT n.organization_id" in query:
            return [{"org": o} for o in sorted({n["organization_id"] for n in self.graph.nodes.values()})]
        return []

    async def scoped_read(self, organization_id, query, params=None, *, timeout=None):
        assert "$organization_id" in query
        self.reads.append((query, {**(params or {}), "organization_id": organization_id}))
        if self.outage:
            raise self.outage
        return self.graph.audit_rows(str(organization_id)) if self.graph is not None else []


def down() -> GraphUnavailable:
    return GraphUnavailable("neo4j unavailable: ServiceUnavailable")


LABEL_RE = re.compile(r"\(\w*:([A-Za-z]+)")


async def build_history(session, org):
    """A known history: protected experiment (observation + outcome + reward), a decided approval, a policy decision, a
    SIMULATED guard check (DELAY vs the protected experiment) and a LIVE external one. Returns a dict of the rows."""
    from datetime import timedelta

    from app.changeguard.service import ChangeInput, submit
    from app.models.interventions import ExperimentOutcome, Reward
    from app.models.policy import PolicyDecision

    from tests import factories as f
    from tests.changeguard.conftest import PAGE, protecting_experiment
    from tests.reliability.helpers import add_obs

    cluster = await f.make_prompt_cluster(session, org)
    inc, iv, exp = await protecting_experiment(session, org, cluster=cluster)
    await f.make_approval(session, iv, status="approved", action_digest="a" * 64)
    await add_obs(session, exp, f.NOW)
    session.add(ExperimentOutcome(experiment_id=exp.id, outcome="favorable", reward_total=0.5, causal_confidence="low"))
    session.add(Reward(experiment_id=exp.id, total=0.5, components={}, weights={}))
    pv = await f.make_policy_version(session)
    session.add(PolicyDecision(incident_id=inc.id, policy_version_id=pv.id, selected_action="observe", probability=0.5,
                               selection_basis="cold_start_prior"))
    await session.commit()
    sim = await submit(session, ChangeInput(org_id=org.id, agent_id="sim-agent", agent_name="Sim", action_type="update_existing_page",
                                            target_url=PAGE, source_mode="SIMULATED", profound_run_id="run-sim-1",
                                            proposed_claims=["SSO is available."], prompt_cluster_ids=[str(cluster.id)]))
    live = await submit(session, ChangeInput(org_id=org.id, agent_id="prof-agent-9", agent_name="Live", action_type="update_existing_page",
                                             target_url="https://testco.example/other", source_mode="LIVE",
                                             profound_run_id="run-live-77", proposed_claims=["Pricing starts at $10."]))
    await session.commit()
    _ = timedelta
    return {"inc": inc, "iv": iv, "exp": exp, "cluster": cluster, "sim": sim, "live": live, "pv": pv}
