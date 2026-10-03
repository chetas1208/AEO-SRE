"""Connector-only OpenAPI document for Meta submission."""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.openapi.utils import get_openapi

router = APIRouter(include_in_schema=False)


def muse_openapi(app) -> dict:
    full = app.openapi()
    paths = {}
    for p, v in full.get("paths", {}).items():
        if p.startswith("/api/muse/v1") or p.startswith("/oauth") or p.startswith("/.well-known/oauth"):
            paths[p] = v
    return {
        "openapi": full.get("openapi", "3.1.0"),
        "info": {"title": "AgentMatch Muse Connector", "version": "1.0.0",
                 "description": "OAuth-protected Muse connector API (read tools + feedback write)."},
        "paths": paths,
        "components": full.get("components", {}),
    }


def register_muse_openapi(app) -> None:
    @app.get("/muse-openapi.json", include_in_schema=False)
    async def _doc():
        return muse_openapi(app)
