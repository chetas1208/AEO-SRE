"""Change Guard test builders (RECORDED/TEST ONLY data; the clock is pinned with the window service's `use_clock`)."""
from __future__ import annotations

from datetime import timedelta

import pytest
from app.core.clock import FixedClock
from app.core.config import get_settings
from app.experiments import window as vwindow

from tests import factories as f
from tests.reliability.helpers import CHANGE, executed_experiment

TOKEN = "test-change-guard-token-0123456789"
PAGE = "https://testco.example/enterprise/security"
H = {"X-Actor": "alice@testco.example"}


@pytest.fixture(autouse=True)
def pinned_clock():
    with vwindow.use_clock(FixedClock(f.NOW)):
        yield f.NOW


@pytest.fixture
def token(monkeypatch):
    monkeypatch.setenv("CHANGE_GUARD_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield TOKEN
    monkeypatch.delenv("CHANGE_GUARD_TOKEN", raising=False)
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _reset_rate_limiter():
    from app.api.routes import change_checks

    change_checks._hits.clear()
    yield
    change_checks._hits.clear()


async def protecting_experiment(session, org, *, target=PAGE, action="update_existing_page", delay_hours=48,
                                executed_at=None, status="awaiting_verification", **kw):
    """An experiment awaiting verification whose scope is `target` (window opens executed_at + delay)."""
    change = {**CHANGE, "target_url": target} if target else {"kind": "observe", "files": []}
    cluster = kw.pop("cluster", None)
    inc, iv, exp = await executed_experiment(
        session, org, executed_at=executed_at or f.NOW - timedelta(hours=1), delay_hours=delay_hours, status=status,
        proposed_change=change, target_key=f"target:{target.lower()}" if target else None, **kw)
    if cluster is not None:
        inc.prompt_cluster_id = cluster.id
        await session.commit()
    return inc, iv, exp


def body(org, **kw):
    """A valid POST /api/change-checks body."""
    out = {"org_id": str(org.id), "agent": {"id": "agent-7", "name": "Content Agent"}, "source_mode": "SIMULATED",
           "target_url": "https://testco.example/pricing", "action_type": "update_existing_page",
           "proposed_claims": ["SAML SSO is available on the Enterprise plan."], "reason": "keep pricing accurate",
           "expected_kpi": "visibility", "risk": "low", "reversible": True}
    out.update(kw)
    return out
