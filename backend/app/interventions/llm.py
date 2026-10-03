"""Small LLM seam for patch generation: protocol + pydantic output schema. Provider access lives in connectors/llm."""
from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.connectors.llm.errors import LLMUnavailable  # re-export: one typed error for "model unreachable"

__all__ = ["DraftClaim", "DraftFile", "LLMClient", "LLMUnavailable", "PatchDraft", "default_llm_client"]


@runtime_checkable
class LLMClient(Protocol):
    async def complete_json(self, *, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        """Return a JSON object conforming to `schema` (raise LLMUnavailable if the model cannot be reached)."""
        ...


class DraftFile(BaseModel):
    path: str
    new_content: str = Field(description="Markdown body only: no section markers, no front matter")
    change_type: str = "update"


class DraftClaim(BaseModel):
    text: str
    fact_ids: list[str] = Field(default_factory=list)


class PatchDraft(BaseModel):
    action: str | None = Field(default=None, description="Echo of the policy-selected action; must equal it exactly")
    title: str = Field(max_length=200)
    summary: str = Field(max_length=1000)
    files: list[DraftFile]
    claims: list[DraftClaim]


def default_llm_client() -> LLMClient | None:
    """Configured provider-agnostic client (A15 connector), or None -> callers use deterministic templates."""
    from app.connectors.llm import get_llm_client

    return get_llm_client()
