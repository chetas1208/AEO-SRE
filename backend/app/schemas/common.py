"""Shared response primitives. Wire format is snake_case."""
import uuid
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ErrorBody(ApiModel):
    type: str = Field(description="not_found|validation_error|illegal_transition|conflict|unavailable")
    message: str
    details: Any | None = None
    request_id: str | None = None


class ErrorResponse(ApiModel):
    error: ErrorBody


class Page[T](ApiModel):
    items: list[T]
    total: int
    limit: int
    offset: int


class JobRef(ApiModel):
    job_id: uuid.UUID | None = None
    kind: str
    status: str = Field(description="queued|running|success|failed|unavailable")
    error: str | None = None


class ErrorBody(ApiModel):
    code: str  # stable machine code, e.g. EXPERIMENT_NOT_VERIFIABLE_YET; clients branch on this first
    type: str  # legacy lowercase alias, kept for older clients
    message: str
    details: Any = None
    request_id: str | None = None


class ErrorEnvelope(ApiModel):
    error: ErrorBody
