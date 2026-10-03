"""Typed graph errors. Messages are always scrubbed of credentials before they are constructed."""
from __future__ import annotations

import re

_USERINFO = re.compile(r"((?:neo4j|bolt)(?:\+s{1,2}c?)?://)[^/\s@]+@", re.I)


def scrub(text: str, *secrets: str) -> str:
    """Remove configured secrets and URI userinfo from free text."""
    out = str(text)
    for sec in secrets:
        if sec and len(sec) >= 4:
            out = out.replace(sec, "[redacted]")
    return _USERINFO.sub(r"\1[redacted]@", out)


class GraphError(Exception):
    """Base class. Never carries credentials."""


class GraphUnavailable(GraphError):
    """Neo4j cannot serve this request right now (down, timeout, pool exhausted, not configured, auth failed).
    Callers must degrade (Postgres keeps working; policy falls back to BaselinePolicy), never crash."""

    state = "DEGRADED"


class GraphNotConfigured(GraphUnavailable):
    state = "NOT_CONFIGURED"


class GraphAuthFailed(GraphUnavailable):
    state = "AUTH_FAILED"


class GraphTimeout(GraphUnavailable):
    state = "DEGRADED"


class GraphQueryError(GraphError):
    """The server rejected the query itself (syntax, constraint violation, missing procedure). Not an outage."""

    def __init__(self, message: str, code: str | None = None):
        super().__init__(message)
        self.code = code


class OrganizationScopeError(GraphError):
    """A graph query was attempted without an organization_id (or its Cypher does not reference it)."""


def require_confirm_error() -> GraphError:
    return GraphError("destructive graph operation requires the explicit confirmation string")
