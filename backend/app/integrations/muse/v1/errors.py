from __future__ import annotations

from fastapi import HTTPException


def muse_error(code: str, message: str, status: int = 400) -> HTTPException:
    return HTTPException(status_code=status, detail={"error": {"code": code, "message": message}})
