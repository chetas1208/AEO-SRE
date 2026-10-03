"""Muse connector HTTP surface. Thin shim over `app.integrations.muse.adapter` (all logic lives there).

POST /muse/tools/<tool>  (bearer key; one org)        GET /muse/manifest (public)
Approve / apply / execute are intentionally not exposed (Sensitive Writes).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.deps import SessionDep
from app.core.config import get_settings
from app.integrations.muse.adapter import dispatch
from app.integrations.muse.manifest import build_manifest
from app.integrations.muse.tools import TOOLS, ToolSpec

router = APIRouter(prefix="/muse", tags=["muse-connector"])


@router.get("/manifest")
async def muse_manifest() -> dict[str, Any]:
    """Public tool manifest (names, classification, JSON schemas, auth scheme). No secrets."""
    return build_manifest(get_settings())


def _make_endpoint(spec: ToolSpec):
    async def endpoint(request: Request, session: SessionDep) -> JSONResponse:
        declared = request.headers.get("content-length")
        r = await dispatch(
            spec.slug, await request.body(), authorization=request.headers.get("authorization"),
            source_mode_header=request.headers.get("x-muse-source-mode"),
            content_length=int(declared) if declared and declared.isdigit() else None,
            client=request.client.host if request.client else "unknown",
            request_id=getattr(request.state, "request_id", None), session=session)
        return JSONResponse(r.body, status_code=r.status, headers=r.headers)

    endpoint.__name__ = f"muse_tool_{spec.name}"
    return endpoint


for _spec in TOOLS:
    router.add_api_route(
        _spec.path.removeprefix("/muse"), _make_endpoint(_spec), methods=["POST"], name=_spec.name, summary=_spec.name,
        description=f"[{_spec.classification.upper()}] {_spec.description}",
        openapi_extra={"requestBody": {"required": True, "content": {"application/json": {
            "schema": _spec.input_model.model_json_schema()}}}})
