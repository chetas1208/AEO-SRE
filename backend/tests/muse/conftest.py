"""Muse connector test builders. All data is TEST ONLY; no real call to Meta is ever made."""
from __future__ import annotations

import pytest
from app.changeguard import canonical
from app.core.clock import FixedClock
from app.core.config import get_settings
from app.experiments import window as vwindow
from app.integrations.muse import perception
from app.integrations.muse.ratelimit import LIMITER

from tests import factories as f

KEY = "muse-test-key-0123456789abcdef"
AUTH = {"Authorization": f"Bearer {KEY}"}
SIM = {**AUTH, "X-Muse-Source-Mode": "SIMULATED"}
ACTOR = "alice@testco.example"


@pytest.fixture(autouse=True)
def pinned_clock():
    with vwindow.use_clock(FixedClock(f.NOW)):
        yield f.NOW


@pytest.fixture(autouse=True)
def _reset():
    LIMITER.clear()
    perception.set_perception_provider(None)
    yield
    LIMITER.clear()
    perception.set_perception_provider(None)
    get_settings.cache_clear()


@pytest.fixture
def muse_env(monkeypatch, org):
    """Connector configured and bound to `org`."""
    monkeypatch.setenv("MUSE_CONNECTOR_API_KEY", KEY)
    monkeypatch.setenv("MUSE_ORGANIZATION_ID", str(org.id))
    monkeypatch.setenv("MUSE_RATE_LIMIT_PER_MINUTE", "1000")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def claim(session, org, key="saml-plan", statement="SAML SSO is available only on the Enterprise plan.", **kw):
    c = await canonical.create_claim(session, org.id, ACTOR, key=key, statement=statement, **kw)
    await session.commit()
    return c


async def count(session, *models) -> list[int]:
    from sqlalchemy import func, select

    return [(await session.execute(select(func.count()).select_from(m))).scalar_one() for m in models]
