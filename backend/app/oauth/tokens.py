from __future__ import annotations

import hashlib
import secrets


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def new_opaque_token() -> str:
    return secrets.token_urlsafe(32)


def new_auth_code() -> str:
    return secrets.token_urlsafe(32)
