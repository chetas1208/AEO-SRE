import uuid

from app.measurement.validation_snapshot import deterministic_after_metrics, is_validation_run
from app.models.core import Incident
from app.models.interventions import Experiment


def test_live_ui_incident_is_not_validation_run():
    inc = Incident(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        title="t",
        context={"source": "live", "run_mode": "live"},
    )
    exp = Experiment(
        id=uuid.uuid4(),
        incident_id=inc.id,
        intervention_id=uuid.uuid4(),
        proposed_change={"target_url": "https://profound.academy/test-url-123"},
        before_metrics={"visibility": 58.0},
        selected_action="update_existing_page",
    )
    assert is_validation_run(exp, inc) is False


def test_explicit_test_run_mode_only():
    inc = Incident(id=uuid.uuid4(), org_id=uuid.uuid4(), title="t", context={"run_mode": "test"})
    exp = Experiment(
        id=uuid.uuid4(),
        incident_id=inc.id,
        intervention_id=uuid.uuid4(),
        before_metrics={"visibility": 58.0},
        selected_action="update_existing_page",
    )
    assert is_validation_run(exp, inc) is True


def test_deterministic_after_moves_primary_metric_up():
    exp = Experiment(
        id=uuid.uuid4(),
        incident_id=uuid.uuid4(),
        intervention_id=uuid.uuid4(),
        before_metrics={"visibility": 58.0, "citation_share": 24.0},
        spec={"primary_metric": "visibility", "direction": "increase"},
        selected_action="update_existing_page",
    )
    after = deterministic_after_metrics(exp)
    assert after["visibility"] > 58.0
    assert after["citation_share"] >= 24.0
