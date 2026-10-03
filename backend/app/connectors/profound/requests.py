"""Pydantic request models for the Profound endpoints this connector uses (validated BEFORE sending).

Field names/enums mirror the published OpenAPI spec (External API 0.60.2, see docs/notes/handoff-a7.md).
Only read-only endpoints are modelled; the connector never mutates Profound state.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

FilterOp = Literal[
    "is", "not_is", "in", "not_in", "contains", "not_contains", "matches",
    "contains_case_insensitive", "not_contains_case_insensitive", "exists",
]


class FilterNode(BaseModel):
    """A leaf (field/op/value) or an and/or/not group. Max depth 3 per docs."""

    model_config = ConfigDict(extra="forbid")
    and_: list[FilterNode] | None = Field(default=None, alias="and")
    or_: list[FilterNode] | None = Field(default=None, alias="or")
    not_: FilterNode | None = Field(default=None, alias="not")
    field: str | None = None
    op: FilterOp | None = None
    value: Any = None

    def dump(self) -> dict[str, Any]:
        return self.model_dump(by_alias=True, exclude_none=True)


class _Report(BaseModel):
    model_config = ConfigDict(extra="forbid")
    category_id: str
    start_date: str
    end_date: str

    @field_validator("category_id")
    @classmethod
    def _cat(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("category_id required")
        return v

    @field_validator("start_date", "end_date")
    @classmethod
    def _date(cls, v: str) -> str:
        if not _DATE_RE.match(v):
            raise ValueError("dates must be YYYY-MM-DD (Eastern Time)")
        date.fromisoformat(v)
        return v

    @model_validator(mode="after")
    def _order(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date (v2 end_date is inclusive)")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True, by_alias=True)


class _Paged(_Report):
    cursor: str | None = None
    limit: int | None = Field(default=None, gt=0, le=50)


class VisibilityQuery(_Paged):
    """POST /v2/reports/visibility (v2 beta)."""

    metrics: list[Literal["visibility_score", "share_of_voice", "average_position"]] | None = None
    group_by: list[Literal["date", "model", "topic", "region", "prompt", "persona"]] | None = None
    interval: Literal["day", "week", "month"] = "day"
    scope: Literal["owned", "all"] = "owned"
    assets: str | list[str] | None = None
    filter: FilterNode | None = None

    def payload(self) -> dict[str, Any]:
        out = super().payload()
        if self.filter is not None:
            out["filter"] = self.filter.dump()
        return out


class CitationsQuery(_Paged):
    """POST /v2/reports/citations (v2 beta)."""

    entity: Literal["domain", "page", "citation_category"] | None = None
    metrics: list[Literal["count", "citation_share", "rank", "first_cited_at"]] | None = None
    group_by: list[Literal["page", "date", "model", "topic", "region", "persona", "prompt"]] | None = None
    interval: Literal["day", "week", "month"] = "day"
    scope: Literal["owned", "all"] = "owned"
    filter: FilterNode | None = None

    def payload(self) -> dict[str, Any]:
        out = super().payload()
        if self.filter is not None:
            out["filter"] = self.filter.dump()
        return out


class FactCheckQuery(_Paged):
    """POST /v2/reports/factcheck (v2 beta)."""

    group_by: list[Literal["date", "model", "region", "persona", "prompt", "topic", "tag", "citation", "theme"]] | None = None
    filter: FilterNode | None = None

    def payload(self) -> dict[str, Any]:
        out = super().payload()
        if self.filter is not None:
            out["filter"] = self.filter.dump()
        return out


class FactCheckClaimsQuery(_Report):
    """POST /v2/reports/factcheck/claims (v2 beta). Page size cap is 100 (claims per page)."""

    group_by: list[Literal["model", "region", "persona", "prompt", "topic", "tag", "theme"]] | None = None
    include: list[Literal["theme", "reasoning", "models", "evidence", "citation_sources"]] | None = None
    limit: int | None = Field(default=None, gt=0, le=100)
    cursor: str | None = None


class VolumeQuery(BaseModel):
    """POST /v2/prompt-volumes/volume/on-the-fly. Counts against 1,000 distinct keywords per UTC day."""

    model_config = ConfigDict(extra="forbid")
    keyword: str = Field(min_length=1)
    matching_type: Literal["exact_match", "phrase_match"] = "phrase_match"
    start_date: date
    end_date: date
    regions: list[str] | None = None
    platforms: list[Literal["chatgpt.com", "gemini.google.com", "perplexity.ai"]] | None = None

    @model_validator(mode="after")
    def _order(self):
        if self.start_date > self.end_date:
            raise ValueError("start_date must be <= end_date")
        return self

    def payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


_ANSWER_FIELDS = Literal[
    "run_id", "date", "model", "topic", "topic_id", "persona", "region", "tags", "prompt", "prompt_id",
    "response", "mentions", "citations", "citation_details", "search_queries", "analysis_types", "sentiment_claims",
]


class AnswersQuery(_Report):
    """POST /v2/prompts/answers. Read-only. Page size max 200. Not part of routine ingest."""

    include: list[_ANSWER_FIELDS] | None = None
    filter: FilterNode | None = None
    limit: int | None = Field(default=None, gt=0, le=200)
    cursor: str | None = None

    def payload(self) -> dict[str, Any]:
        out = super().payload()
        if self.filter is not None:
            out["filter"] = self.filter.dump()
        return out


class QueryFanoutsQuery(_Paged):
    """POST /v2/reports/query-fanouts. Read-only. Page size max 50."""

    metrics: list[Literal["fanouts_per_execution", "total_fanouts", "share", "query_variations"]] | None = None
    group_by: list[Literal["date", "model", "region", "prompt", "query"]] | None = None
    interval: Literal["day", "week", "month"] = "day"


class PromptsListQuery(BaseModel):
    """GET /v1/org/categories/{id}/prompts query params."""

    model_config = ConfigDict(extra="forbid")
    limit: int = Field(default=1000, gt=0, le=10000)
    cursor: str | None = None
    status: list[Literal["active", "disabled"]] | None = None
    analysis_type: list[Literal["visibility", "sentiment", "sentiment_v2", "accuracy"]] | None = None

    def params(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


FilterNode.model_rebuild()
