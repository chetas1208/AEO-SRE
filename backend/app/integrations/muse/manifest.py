"""Public tool manifest, generated from the same ToolSpec objects and pydantic models the tools run on."""
from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.integrations.muse.errors import ERROR_DESCRIPTIONS
from app.integrations.muse.tools import TOOLS, ToolSpec

MANIFEST_VERSION = "1.0"


def tool_entry(t: ToolSpec) -> dict[str, Any]:
    return {
        "name": t.name, "description": t.description, "classification": t.classification, "method": "POST",
        "path": t.path, "scope": t.scope, "side_effects": t.side_effects,
        "input_schema": t.input_model.model_json_schema(), "output_schema": t.output_model.model_json_schema(),
        "errors": list(t.errors),
    }


def build_manifest(settings: Settings) -> dict[str, Any]:
    return {
        "name": "AEO SRE Change Guard", "version": MANIFEST_VERSION, "transport": "https+json",
        "auth": {"scheme": "bearer", "header": "Authorization", "format": "Bearer <connector key>",
                 "organization_binding": "one key maps to exactly one organization (server configuration)"},
        "rate_limit": {"per_minute_per_key": settings.muse_rate_limit_per_minute, "on_exceed": "429 + Retry-After"},
        "limits": {"max_body_bytes": 65536, "request_timeout_seconds": settings.muse_request_timeout_s,
                   "max_intent_ttl_seconds": 86400},
        "error_envelope": {"error": {"code": "string", "type": "string", "message": "string", "details": "any",
                                     "request_id": "string"}},
        "error_codes": ERROR_DESCRIPTIONS,
        "not_exposed": ["approve", "apply", "execute", "retire", "delete"],
        "tools": [tool_entry(t) for t in TOOLS],
    }
