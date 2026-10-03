"""Guard reliability fixtures. Whole directory skips until Change Guard (G1) exists."""
from __future__ import annotations

import pytest
import pytest_asyncio

try:
    import app.changeguard.service  # noqa: F401

    _BUILT = True
except ImportError:
    _BUILT = False


def pytest_collection_modifyitems(config, items):
    if _BUILT:
        return
    skip = pytest.mark.skip(reason="Change Guard (G1) not built yet: app.changeguard.service missing")
    for it in items:
        if "guard_reliability" in str(it.fspath):
            it.add_marker(skip)


from app.core.config import get_settings  # noqa: E402

from tests.guard_reliability.support import TOKEN  # noqa: E402


@pytest.fixture(autouse=True)
def guard_token(monkeypatch):
    monkeypatch.setenv("CHANGE_GUARD_TOKEN", TOKEN)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest_asyncio.fixture
async def client(app_client):
    return app_client
