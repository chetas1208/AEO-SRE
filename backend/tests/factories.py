"""Test-only data factories (RECORDED/TEST ONLY: nothing here is ever shown as live data).

All factories persist with commit so other sessions (SSE, services) see the rows. Models owned by other
agents are imported lazily so this module imports even while those modules are still landing.
"""
from __future__ import annotations

import itertools
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from app.domain.enums import (
    ActionType,
    EvidenceStatus,
    EvidenceType,
    HypothesisStatus,
    IncidentCategory,
    IncidentState,
    Risk,
    Severity,
)

NOW = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
_counter = itertools.count(1)


async def _persist(session, *objs, commit: bool = True):
    session.add_all(objs)
    await (session.commit() if commit else session.flush())
    return objs[0] if len(objs) == 1 else objs


async def make_org(session, **kw) -> Any:
    from app.models.core import Organization

    n = next(_counter)
    defaults = dict(
        name=f"TestCo {n}",
        domain=f"testco{n}.example",
        competitor_domains=["rival.example"],
        canonical_domains=[f"testco{n}.example"],
        personas=["IT security lead"],
        topics=["Enterprise SSO"],
    )
    return await _persist(session, Organization(**{**defaults, **kw}))


async def make_prompt_cluster(session, org, topic: str = "Enterprise SSO", prompts: list | None = None) -> Any:
    from app.models.core import PromptCluster

    return await _persist(
        session,
        PromptCluster(
            org_id=org.id, topic=topic,
            prompts=prompts or [f"best {topic} tools", f"does TestCo support {topic}?"],
        ),
    )


async def make_signal(session, org, *, metric: str = "visibility", value: float, observed_at: datetime,
                      kind: str = "visibility", cluster=None, source: str = "profound", raw: dict | None = None,
                      baseline: float | None = None, commit: bool = True) -> Any:
    from app.models.core import Signal

    return await _persist(
        session,
        Signal(org_id=org.id, kind=kind, source=source, metric=metric, value=value, baseline=baseline,
               observed_at=observed_at, prompt_cluster_id=cluster.id if cluster else None,
               raw=raw or {"fixture": "RECORDED/TEST ONLY"}),
        commit=commit,
    )


async def make_signal_series(session, org, *, metric: str = "visibility", values: list[float],
                             end: datetime = NOW, step: timedelta = timedelta(days=1), cluster=None,
                             kind: str | None = None) -> list[Any]:
    """One signal per value, oldest first, last value observed at `end`."""
    from app.models.core import Signal

    start = end - step * (len(values) - 1)
    rows = [
        Signal(org_id=org.id, kind=kind or metric, source="profound", metric=metric, value=v,
               observed_at=start + step * i, prompt_cluster_id=cluster.id if cluster else None,
               raw={"fixture": "RECORDED/TEST ONLY"})
        for i, v in enumerate(values)
    ]
    session.add_all(rows)
    await session.commit()
    return rows


def visibility_drop_values(before: float = 61.0, after: float = 37.0, baseline_days: int = 14, jitter: float = 1.0) -> list[float]:
    """Stable baseline (tiny deterministic wiggle) then a step drop in the final observation."""
    wiggle = [0.0, jitter, -jitter, jitter / 2, -jitter / 2]
    return [before + wiggle[i % len(wiggle)] for i in range(baseline_days)] + [after]


async def make_incident(session, org, **kw) -> Any:
    from app.models.core import Incident

    n = next(_counter)
    defaults = dict(
        org_id=org.id,
        title=f"Test incident {n}",
        category=IncidentCategory.VISIBILITY_DROP.value,
        severity=Severity.HIGH.value,
        priority=62.0,
        priority_breakdown={},
        state=IncidentState.DETECTED.value,
        detected_at=NOW,
        first_observed_at=NOW - timedelta(days=1),
        metrics=[],
        confidence=0.7,
        summary="RECORDED/TEST ONLY incident",
        context={},
    )
    for k in ("category", "severity", "state"):
        v = kw.get(k)
        if hasattr(v, "value"):
            kw[k] = v.value
    return await _persist(session, Incident(**{**defaults, **kw}))


async def make_evidence(session, incident, **kw) -> Any:
    from app.models.evidence import Evidence

    n = next(_counter)
    defaults = dict(
        incident_id=incident.id,
        type=EvidenceType.OWNED.value,
        status=EvidenceStatus.LIVE.value,
        title=f"Evidence {n}",
        source="https://testco.example/docs/sso",
        url="https://testco.example/docs/sso",
        content_hash=f"{n:064x}",  # bare sha256 hex, no prefix
        excerpt="RECORDED/TEST ONLY excerpt",
        retrieval_method="http_get",
        retrieved_at=NOW,
        observed_at=NOW,
        support_score=0.8,
        contradiction_score=0.05,
        insufficient_score=0.15,
        freshness_risk=0.2,
        confidence=0.8,
        raw={"fixture": "RECORDED/TEST ONLY"},
    )
    for k in ("type", "status"):
        v = kw.get(k)
        if hasattr(v, "value"):
            kw[k] = v.value
    return await _persist(session, Evidence(**{**defaults, **kw}))


async def make_hypothesis(session, incident, evidence_ids: list | None = None, **kw) -> Any:
    from app.models.evidence import Hypothesis

    n = next(_counter)
    defaults = dict(
        incident_id=incident.id,
        title=f"Hypothesis {n}",
        summary="RECORDED/TEST ONLY",
        status=HypothesisStatus.PROPOSED.value,
        confidence=0.6,
        evidence_ids=[str(e) for e in (evidence_ids or [])],
        rationale="",
        produced_by="rules",
    )
    v = kw.get("status")
    if hasattr(v, "value"):
        kw["status"] = v.value
    return await _persist(session, Hypothesis(**{**defaults, **kw}))


async def make_intervention(session, incident, hypothesis=None, **kw) -> Any:
    from app.models import interventions as m

    n = next(_counter)
    defaults = dict(
        incident_id=incident.id,
        hypothesis_id=hypothesis.id if hypothesis else None,
        action=ActionType.UPDATE_EXISTING_PAGE.value,
        title=f"Intervention {n}",
        rationale="RECORDED/TEST ONLY",
        risk=Risk.LOW.value,
        proposed_change={"files": [{"path": "docs/sso.md", "diff": "+ SAML SSO supported"}]},
        score=0.5,
        selected=False,
    )
    for k in ("action", "risk"):
        v = kw.get(k)
        if hasattr(v, "value"):
            kw[k] = v.value
    return await _persist(session, m.Intervention(**{**defaults, **kw}))


async def make_approval(session, intervention, *, status: str = "pending", decided_by: str | None = None, **kw) -> Any:
    from app.models import interventions as m

    decided = status != "pending"
    return await _persist(
        session,
        m.Approval(intervention_id=intervention.id, status=status,
                   decided_by=decided_by or ("tester@example.com" if decided else None),
                   decided_actor_type="human" if decided else None,
                   decided_at=NOW if decided else None, **kw),
    )


async def make_experiment(session, incident, intervention, policy_version=None, **kw) -> Any:
    from app.models import interventions as m

    defaults = dict(
        incident_id=incident.id,
        intervention_id=intervention.id,
        selected_action=intervention.action,
        policy_version_id=policy_version.id if policy_version else None,
        policy_probability=0.5,
        context_vector={},
        alternatives=[],
        evidence_snapshot={},
        before_metrics={"visibility": 0.37},
        after_metrics=None,
        status="awaiting_verification",
    )
    return await _persist(session, m.Experiment(**{**defaults, **kw}))


async def make_policy_version(session, **kw) -> Any:
    from app.policy.store import PolicyStore

    store = PolicyStore(session)
    row = await store.ensure_initial()
    await session.commit()
    return row


def utc(days_ago: float = 0.0) -> datetime:
    return NOW - timedelta(days=days_ago)


def new_uuid() -> uuid.UUID:
    return uuid.uuid4()
