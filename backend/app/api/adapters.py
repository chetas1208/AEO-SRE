"""Lazy bridges to other agents' modules. Missing module -> NotImplementedYet / empty, never fake data."""

import importlib
from typing import Any

from app.api.errors import NotImplementedYet


def optional(module: str) -> Any | None:
    try:
        return importlib.import_module(module)
    except ImportError:
        return None


def require(module: str, purpose: str) -> Any:
    mod = optional(module)
    if mod is None:
        raise NotImplementedYet(f"{purpose} is unavailable: module {module} has not been installed yet")
    return mod


def interventions_models() -> Any | None:
    return optional("app.models.interventions")


def policy_models() -> Any | None:
    return optional("app.models.policy")
