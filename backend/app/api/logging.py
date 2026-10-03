import logging
import re
import sys

import structlog

SENSITIVE_KEYS = {"authorization", "proxy-authorization", "api_key", "apikey", "x-api-key", "password", "secret", "token",
                  "access_token", "auth_token", "cookie", "set-cookie"}
_URI_USERINFO = re.compile(r"((?:neo4j|bolt)(?:\+s{1,2}c?)?://)[^/\s@]+@")
SENSITIVE_SUFFIXES = ("_api_key", "_secret", "_password")


def _configured_secrets() -> list[str]:
    from app.core.config import get_settings

    s = get_settings()
    vals = [getattr(s, n, "") for n in ("profound_api_key", "model_api_key", "github_token", "change_guard_token", "muse_connector_api_key",
                                      "neo4j_password", "neo4j_aura_api_client_secret")]
    return [v for v in vals if isinstance(v, str) and len(v) >= 8]


def _scrub(value, secrets: list[str], key: str = ""):
    k = key.lower()
    if (k in SENSITIVE_KEYS or k.endswith(SENSITIVE_SUFFIXES)) and value not in (None, "", False):
        return "[redacted]"
    if isinstance(value, str):
        for sec in secrets:
            value = value.replace(sec, "[redacted]")
        return _URI_USERINFO.sub(r"\1[redacted]@", value)
    if isinstance(value, dict):
        return {k: _scrub(v, secrets, str(k)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_scrub(v, secrets, key) for v in value]
    return value


def redact_secrets(logger, method, event_dict):
    """Last line of defence: no configured credential and no Authorization-like field ever leaves in a log line."""
    secrets = _configured_secrets()
    return {k: _scrub(v, secrets, k) for k, v in event_dict.items()}


def configure_logging(level: str = "INFO", json_logs: bool = False) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(level=lvl, stream=sys.stdout, format="%(message)s")
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            redact_secrets,
            structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(lvl),
        cache_logger_on_first_use=True,
    )
