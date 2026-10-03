"""`Experiment.after_metrics` can be set ONLY through the verification path.

`verification_path()` is entered by `app.experiments.verification.apply_measurement` (the single writer). Any other
assignment to `after_metrics` on a persisted experiment raises `UnverifiedAfterMetrics`. Rows being created (tests,
backfills) are exempt: the guard targets the update path, where a bypass would fake a measured outcome.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar

from sqlalchemy import event, inspect

from app.models.interventions import Experiment

_in_path: ContextVar[bool] = ContextVar("verification_path", default=False)


class UnverifiedAfterMetrics(RuntimeError):
    """after_metrics was assigned outside the verification path."""


@contextlib.contextmanager
def verification_path() -> Iterator[None]:
    token = _in_path.set(True)
    try:
        yield
    finally:
        _in_path.reset(token)


@event.listens_for(Experiment.after_metrics, "set")
def _guard_after_metrics(target: Experiment, value, oldvalue, initiator) -> None:  # noqa: ARG001
    state = inspect(target)
    if state.persistent and not _in_path.get() and value != oldvalue:
        raise UnverifiedAfterMetrics("after_metrics can only be set by the verification service")
