from datetime import datetime

from app.domain.enums import CapabilityState
from app.schemas.common import ApiModel
from app.schemas.organizations import OrganizationOut, OrganizationUpdate
from app.schemas.policy import PolicyOut, PolicySettingsUpdate


class IntegrationStatus(ApiModel):
    key: str
    label: str
    connected: bool
    state: CapabilityState
    configured_fields: list[str] = []
    missing_fields: list[str] = []
    last_success: datetime | None = None
    last_error: str | None = None
    detail: str | None = None
    optional: bool = False  # optional integrations (e.g. the GitHub executor) are never alarming when unset
    kind: str = "integration"  # integration | executor


class SettingsOut(ApiModel):
    organization: OrganizationOut | None = None
    organizations: list[OrganizationOut] = []
    integrations: list[IntegrationStatus]
    policy: PolicyOut
    environment: str


class SettingsUpdate(ApiModel):
    org_id: str | None = None
    organization: OrganizationUpdate | None = None
    policy: PolicySettingsUpdate | None = None
