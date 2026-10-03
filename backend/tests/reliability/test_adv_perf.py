"""Pathological-path guard: read endpoints must not issue a query per child row (N+1)."""
from __future__ import annotations

import pytest
from app.core import db as appdb
from sqlalchemy import event

from tests import factories as f


class QueryCounter:
    def __init__(self):
        self.n = 0

    def __enter__(self):
        eng = appdb.get_engine().sync_engine
        self._eng = eng
        event.listen(eng, "before_cursor_execute", self._hit)
        return self

    def _hit(self, *a, **k):
        self.n += 1

    def __exit__(self, *exc):
        event.remove(self._eng, "before_cursor_execute", self._hit)


async def build(session, org, n_children: int):
    inc = await f.make_incident(session, org)
    evs = [await f.make_evidence(session, inc) for _ in range(n_children)]
    hyps = [await f.make_hypothesis(session, inc, evidence_ids=[e.id for e in evs[:2]]) for _ in range(n_children)]
    for h in hyps[:3]:
        await f.make_intervention(session, inc, h)
    return inc


@pytest.mark.parametrize("path", ["/api/incidents/{id}", "/api/incidents/{id}/evidence", "/api/incidents/{id}/hypotheses",
                                  "/api/incidents/{id}/graph"])
async def test_detail_queries_do_not_scale_with_child_rows(app_client, session, org, path):
    small = await build(session, org, 3)
    large = await build(session, org, 30)
    with QueryCounter() as a:
        assert (await app_client.get(path.format(id=small.id))).status_code == 200
    with QueryCounter() as b:
        assert (await app_client.get(path.format(id=large.id))).status_code == 200
    assert b.n <= a.n + 2, f"{path}: {a.n} queries for 3 children but {b.n} for 30 (N+1)"
    assert b.n <= 40, f"{path}: {b.n} queries is pathological"


async def test_incident_and_experiment_lists_do_not_scale_with_rows(app_client, session, org):
    for _ in range(3):
        await f.make_incident(session, org)
    with QueryCounter() as a:
        assert (await app_client.get("/api/incidents")).status_code == 200
    for _ in range(25):
        await f.make_incident(session, org)
    with QueryCounter() as b:
        assert (await app_client.get("/api/incidents", params={"limit": 50})).status_code == 200
    assert b.n <= a.n + 2, f"incident list: {a.n} -> {b.n} queries"


async def test_experiment_list_does_not_scale_with_rows(app_client, session, org):
    from tests.reliability.helpers import executed_experiment

    await executed_experiment(session, org)
    with QueryCounter() as a:
        assert (await app_client.get("/api/experiments")).status_code == 200
    for _ in range(15):
        await executed_experiment(session, org)
    with QueryCounter() as b:
        assert (await app_client.get("/api/experiments")).status_code == 200
    assert b.n <= a.n + 3, f"experiment list: {a.n} -> {b.n} queries (N+1)"
