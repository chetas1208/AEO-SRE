import pytest
from httpx import ASGITransport, AsyncClient

from app.api.main import app
from app.core.config import get_settings
from app.oauth.pkce import verify_pkce
from app.oauth.tokens import hash_secret
import base64
import hashlib
import secrets


def test_pkce_s256_roundtrip():
    verifier = secrets.token_urlsafe(64)[:64]
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    assert verify_pkce(verifier, challenge, "S256")


@pytest.mark.asyncio
async def test_oauth_metadata():
    get_settings.cache_clear()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        r = await client.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    assert "authorization_endpoint" in r.json()
