"""Live cutover guards: redaction, provenance, temporal verification, production fixture refusal."""

from __future__ import annotations

import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from app.connectors.profound.redact import redact_for_display
from app.domain.source_mode import SignalSourceMode, source_mode_for_signal
from app.services.pipeline import due_experiment_ids, verify

from tests.reliability.helpers import executed_experiment

ROOT = Path(__file__).resolve().parents[3]
SEED = ROOT / "scripts" / "seed_dev_fixture.py"
WINDOW = datetime(2026, 10, 4, 20, 29, 43, tzinfo=UTC)


def test_redact_strips_api_key_fields():
    raw = {"headers": {"Authorization": "Bearer secret-token", "X-API-Key": "abc123"}, "ok": True}
    out = redact_for_display(raw)
    assert out["headers"]["Authorization"] == "[REDACTED]"
    assert out["headers"]["X-API-Key"] == "[REDACTED]"
    assert out["ok"] is True


def test_source_mode_distinguishes_fixture_from_live():
    assert source_mode_for_signal("dev_fixture") == SignalSourceMode.FIXTURE
    assert source_mode_for_signal("profound") == SignalSourceMode.LIVE
    assert source_mode_for_signal("historical_replay") == SignalSourceMode.REPLAY


def test_seed_fixture_refuses_production(monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    proc = subprocess.run(
        [sys.executable, str(SEED), "baseline"],
        cwd=ROOT / "backend",
        capture_output=True,
        text=True,
    )
    assert proc.returncode != 0
    assert "production" in (proc.stderr + proc.stdout).lower()


@pytest.mark.asyncio
async def test_verification_refused_one_second_before_window(session, org, monkeypatch):
    executed = WINDOW - timedelta(hours=49)
    _, _, exp = await executed_experiment(
        session, org, executed_at=executed, delay_hours=int((WINDOW - executed).total_seconds() // 3600),
    )
    assert exp.verification_window_start == WINDOW  # the window is frozen once verification started; built via delay_hours

    from app.core.clock import FixedClock
    from app.experiments.window import use_clock

    with use_clock(FixedClock(WINDOW - timedelta(seconds=1))):  # the one window service reads the injected clock
        assert exp.id not in await due_experiment_ids(session)
        out = await verify(session, exp.id)
    assert out["status"] == "awaiting_window"
    assert exp.after_metrics is None

    with use_clock(FixedClock(WINDOW)):  # start is INCLUSIVE
        assert exp.id in await due_experiment_ids(session)
    with use_clock(FixedClock(WINDOW + timedelta(seconds=1))):
        assert exp.id in await due_experiment_ids(session)
