"""Discovery-gap test helpers. Everything here is TEST/SIMULATED: no real Profound call is ever made."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest_asyncio
from app.changeguard import canonical

from tests import factories as f

FIXTURE = json.loads((Path(__file__).parent / "factcheck_claims_test.json").read_text())
HUMAN = "alice@testco.example"
BASE = "https://api.tryprofound.com"
CAT = "cat-test-1"


class FakeProfound:
    """Test double with the ProfoundClient surface the adapter uses. `calls` records every request."""

    def __init__(self, claims: Any = None, answers: Any = None, claims_error: Exception | None = None,
                 answers_error: Exception | None = None):
        self.claims, self.answers_payload = claims, answers
        self.claims_error, self.answers_error = claims_error, answers_error
        self.calls: list[str] = []

    async def factcheck_claims(self, q):
        self.calls.append("factcheck_claims")
        if self.claims_error:
            raise self.claims_error
        return SimpleNamespace(data=self.claims if self.claims is not None else {"data": []}, next_cursor=None)

    async def answers(self, q):
        self.calls.append("answers")
        if self.answers_error:
            raise self.answers_error
        return SimpleNamespace(data=self.answers_payload if self.answers_payload is not None else {"data": []},
                               next_cursor=None)

    async def aclose(self):
        pass


async def add_canonical(session, org, key="saml-enterprise", statement="SAML SSO is available on the Enterprise plan.",
                        **kw):
    c = await canonical.create_claim(session, org.id, HUMAN, key=key, statement=statement, **kw)
    await session.commit()
    return c


@pytest_asyncio.fixture
async def org_b(session):
    return await f.make_org(session)
