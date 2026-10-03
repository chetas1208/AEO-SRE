import uuid
from datetime import datetime

from pydantic import Field

from app.schemas.common import ApiModel, JobRef


class OrganizationCreate(ApiModel):
    domain: str = Field(min_length=3, description="company.com or https://company.com")
    name: str | None = None


class OrganizationUpdate(ApiModel):
    name: str | None = None
    competitor_domains: list[str] | None = None
    canonical_domains: list[str] | None = None
    personas: list[str] | None = None
    topics: list[str] | None = None


class OrganizationOut(ApiModel):
    id: uuid.UUID
    name: str
    domain: str
    competitor_domains: list = []
    canonical_domains: list = []
    personas: list = []
    topics: list = []
    created_at: datetime
    incident_count: int = 0
    signal_count: int = 0
    last_signal_at: datetime | None = None


class OrganizationCreated(OrganizationOut):
    ingest_job: JobRef
