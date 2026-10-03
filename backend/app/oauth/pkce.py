from __future__ import annotations

import base64
import hashlib
import re


def verify_pkce(code_verifier: str, code_challenge: str, method: str) -> bool:
    if method != "S256":
        return False
    if not re.fullmatch(r"[\x21-\x7E]{43,128}", code_verifier or ""):
        return False
    digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return computed == code_challenge
