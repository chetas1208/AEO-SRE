"""Read-only experiment verification snapshot (no mutations).

    cd backend && .venv/bin/python -m app.devtools.experiment_status <experiment-id>
"""

from __future__ import annotations

import asyncio
import json
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.db import get_sessionmaker
from app.domain.enums import ExperimentStatus
from app.experiments.verification import qualifying_observations
from app.models.interventions import Experiment, Observation, Reward


def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=UTC) if dt is not None and dt.tzinfo is None else dt


async def snapshot(experiment_id: uuid.UUID, *, now: datetime | None = None) -> dict:
    from app.experiments import window as vwindow

    now = now or vwindow.now()
    async with get_sessionmaker()() as session:
        exp = await session.get(Experiment, experiment_id)
        if exp is None:
            return {"error": "not_found"}
        start = _aware(exp.verification_window_start)
        eligible = (  # the ONE window service decides; this CLI only reports
            vwindow.attempt_allowed(start, now, dry_run=bool(exp.dry_run)).ok
            and ExperimentStatus(exp.status) == ExperimentStatus.AWAITING_VERIFICATION
        )
        obs_count = len(
            (await session.execute(select(Observation.id).where(Observation.experiment_id == exp.id))).all()
        )
        reward = (
            await session.execute(select(Reward.id, Reward.total).where(Reward.experiment_id == exp.id))
        ).first()
        qualifying = await qualifying_observations(session, exp) if exp else []
        return {
            "id": str(exp.id),
            "status": exp.status,
            "selected_action": exp.selected_action,
            "before_metric_keys": list((exp.before_metrics or {}).keys()),
            "verification_window_start": start.isoformat() if start else None,
            "verification_window_end": (
                exp.verification_window_end.isoformat() if exp.verification_window_end else None
            ),
            "eligible_for_verification_now": eligible,
            "before_metrics": exp.before_metrics,
            "after_metrics": exp.after_metrics,
            "observations_recorded": obs_count,
            "qualifying_observations": len(qualifying),
            "reward_present": reward is not None,
            "now_utc": now.isoformat(),
        }


async def run(experiment_id: uuid.UUID) -> int:
    print(json.dumps(await snapshot(experiment_id), indent=2, default=str))
    return 0


def main() -> None:
    if len(sys.argv) != 2:
        print("usage: python -m app.devtools.experiment_status <experiment-id>")
        raise SystemExit(2)
    raise SystemExit(asyncio.run(run(uuid.UUID(sys.argv[1]))))


if __name__ == "__main__":
    main()
