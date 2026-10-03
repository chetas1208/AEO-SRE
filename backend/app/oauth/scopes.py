from __future__ import annotations

READ_ONLY_BUNDLE = (
    "matches:read",
    "discovery_gaps:read",
    "campaigns:read",
    "experiments:read",
)
ALL_SCOPES = (*READ_ONLY_BUNDLE, "feedback:write")
SCOPE_DESCRIPTIONS = {
    "matches:read": "Find product matches in your workspace",
    "discovery_gaps:read": "Read AI discovery gaps (Profound-backed when configured)",
    "campaigns:read": "Read campaign summaries",
    "experiments:read": "Read experiment status",
    "feedback:write": "Record explicit recommendation feedback",
}


def parse_scope_string(raw: str) -> tuple[str, ...]:
    parts = [p.strip() for p in (raw or "").split() if p.strip()]
    return tuple(dict.fromkeys(parts))


def validate_scopes(requested: tuple[str, ...]) -> tuple[str, ...]:
    bad = [s for s in requested if s not in ALL_SCOPES]
    if bad:
        raise ValueError(f"unknown scope(s): {', '.join(bad)}")
    return requested


def scopes_allow(required: str, granted: tuple[str, ...]) -> bool:
    return required in granted
