"""Material query-fanout shifts. Tiny wording changes must not become evidence."""
from datetime import UTC, datetime

from app.investigation.fanout import material_fanout_shifts

T0 = datetime(2026, 9, 20, tzinfo=UTC)
T1 = datetime(2026, 9, 25, tzinfo=UTC)


def _row(query, share, at, prompt="Does X support SAML?"):
    return {"query": query, "prompt": prompt, "share": share, "observed_at": at}


def test_small_share_move_is_ignored():
    rows = [_row("enterprise analytics SSO", 0.20, T0), _row("enterprise analytics SSO", 0.22, T1)]
    assert material_fanout_shifts(rows, competitors=["Acme"]) == []


def test_material_share_shift_is_kept():
    rows = [_row("enterprise analytics SSO", 0.10, T0), _row("enterprise analytics SSO", 0.40, T1)]
    shifts = material_fanout_shifts(rows, competitors=["Acme"])
    assert len(shifts) == 1
    assert shifts[0].kind == "share_shift"
    assert shifts[0].before_share == 0.10 and shifts[0].after_share == 0.40


def test_new_competitor_query_with_material_share():
    rows = [_row("acme sso", 0.30, T1)]
    shifts = material_fanout_shifts(rows, competitors=["Acme"])
    assert len(shifts) == 1
    assert shifts[0].kind == "competitor_emerged" and shifts[0].competitor == "Acme"


def test_new_query_below_threshold_is_ignored():
    rows = [_row("acme sso", 0.01, T1)]
    assert material_fanout_shifts(rows, competitors=["Acme"]) == []


def test_percent_scale_uses_the_larger_threshold():
    rows = [_row("enterprise sso", 40, T0), _row("enterprise sso", 48, T1)]
    assert material_fanout_shifts(rows) == []
    rows = [_row("enterprise sso", 20, T0), _row("enterprise sso", 48, T1)]
    assert material_fanout_shifts(rows)[0].kind == "share_shift"
