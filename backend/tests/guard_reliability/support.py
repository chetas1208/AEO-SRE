"""Shared builders for the Change Guard reliability tests.

Written against docs/CHANGE_GUARD_SPEC.md. Response-shape access goes through the small accessors below so a
rename in the real API is a one-line fix. All data is TEST ONLY and labelled SIMULATED.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

from app.models.interventions import Experiment

from tests import factories as f
from tests.reliability.helpers import CHANGE, executed_experiment

TOKEN = "test-guard-token-0123456789abcdef"
TARGET = "https://testco.example/enterprise/security"
URL = "/api/change-checks"
DECISIONS = ("ALLOW", "MERGE", "DELAY", "REQUIRE_REVIEW", "BLOCK")


def changeset(org, **over: Any) -> dict:
    body: dict[str, Any] = {
        "org_id": str(org.id),
        "agent": {"id": "sim-agent-1", "name": "Simulated Citation Agent"},
        "profound_run_id": f"sim-run-{uuid.uuid4().hex[:8]}",
        "source_mode": "SIMULATED",
        "target_url": TARGET,
        "action_type": "update_existing_page",
        "proposed_claims": ["SAML SSO is available on the Enterprise plan."],
        "reason": "simulated citation-gap fix",
        "expected_kpi": "citation_share",
        "risk": "low",
        "reversible": True,
    }
    body.update(over)
    return body


def auth(token: str | None = TOKEN) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"} if token is not None else {}


async def post(client, body: dict, *, token: str | None = TOKEN, headers: dict | None = None):
    return await client.post(URL, json=body, headers={**auth(token), **(headers or {})})


# --- response accessors ------------------------------------------------------------------------------------
def decision(r) -> str:
    return r.json()["decision"]


def findings(r) -> list[dict]:
    return r.json().get("findings") or []


def finding_types(r) -> list[str]:
    return [str(x.get("type", "")).lower() for x in findings(r)]


def has_finding(r, needle: str) -> bool:
    return any(needle in t for t in finding_types(r))


def eligible_after(r) -> str | None:
    d = r.json()
    if d.get("eligible_after"):
        return d["eligible_after"]
    for x in findings(r):
        if x.get("eligible_after"):
            return x["eligible_after"]
    return None


def err_code(r) -> str | None:
    try:
        return r.json()["error"]["code"]
    except Exception:
        return None


# --- seeding ---------------------------------------------------------------------------------------------
async def protected_experiment(session, org, *, target: str = TARGET, status="awaiting_verification",
                               executed_at=None, delay_hours=48, action: str = "update_existing_page"):
    """An experiment that is mid-measurement on `target` (state built directly, as the reliability helpers do)."""
    executed_at = executed_at or f.NOW
    key = f"target:{target.strip().lower().rstrip('/')}"
    inc, iv, exp = await executed_experiment(session, org, executed_at=executed_at, delay_hours=delay_hours,
                                             status=status, target_key=key, selected_action=action)
    iv.proposed_change = {**CHANGE, "target_url": target}
    iv.action = action
    await session.commit()
    return inc, iv, exp


async def experiment_snapshot(session, exp_id) -> dict:
    await session.rollback()
    e = await session.get(Experiment, exp_id)
    await session.refresh(e)
    return {k: getattr(e, k) for k in ("status", "executed_at", "verification_window_start",
                                       "verification_window_end", "after_metrics", "target_key", "selected_action")}


def expected_after(exp, now=None):
    """The only legitimate eligible_after: from the window service data. Not yet started -> window start (the earliest a
    measurement can begin, which is the date the spec quotes for EXP-0001); started but unmeasured -> window end."""
    from datetime import UTC, datetime

    now = now or datetime.now(UTC)
    start, end = exp.verification_window_start, exp.verification_window_end
    return start if now < start else end


def iso_close(a: str, b, tol=timedelta(seconds=2)) -> bool:
    from datetime import datetime

    from app.experiments.window import aware

    x = aware(datetime.fromisoformat(a.replace("Z", "+00:00")))
    return abs(x - aware(b)) <= tol


async def make_canonical(client, org, key: str, statement: str, *, entities=None, scope: str = "org",
                         actor: str = "alice", **extra):
    body = {"key": key, "statement": statement, "entities": entities or [], "scope": scope,
            "source": "test", **extra}
    return await client.post(f"/api/organizations/{org.id}/canonical-claims", json=body,
                             headers={"X-Actor": actor})
