from __future__ import annotations

import pytest
from app.core.config import get_settings


@pytest.fixture(autouse=True)
def _fast_backoff(monkeypatch):
    monkeypatch.setenv("GRAPH_OUTBOX_BACKOFF_BASE_S", "5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
