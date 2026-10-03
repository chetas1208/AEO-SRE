"""Sanity checks for the test harness itself."""
from app.models.core import Organization
from sqlalchemy import select

from tests.helpers import FakeClock, parse_sse


async def test_db_roundtrip_and_isolation(session):
    session.add(Organization(name="Acme", domain="acme.test"))
    await session.commit()
    rows = (await session.execute(select(Organization))).scalars().all()
    assert len(rows) == 1


async def test_db_is_clean_between_tests(session):
    rows = (await session.execute(select(Organization))).scalars().all()
    assert rows == []


def test_fake_clock_advances():
    c = FakeClock()
    t0 = c.now()
    c.advance(hours=2)
    assert (c.now() - t0).total_seconds() == 7200


def test_parse_sse_frames_and_comments():
    frames = parse_sse('id: 3\nevent: step\ndata: {"a": 1}\n\n: ping\n\n')
    assert frames[0]["id"] == "3" and frames[0]["json"] == {"a": 1}
    assert frames[1]["comment"] == ["ping"]
