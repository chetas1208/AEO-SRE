import asyncio

from app.core import events


def test_offer_bounds_queue_and_flags_overflow():
    q: asyncio.Queue = asyncio.Queue(maxsize=2)
    for i in range(5):
        events._offer(q, {"seq": i})
    assert q.qsize() == 2  # never grows past the bound
    assert getattr(q, "overflow", False) is True  # subscriber will be cut off and replay from the DB
