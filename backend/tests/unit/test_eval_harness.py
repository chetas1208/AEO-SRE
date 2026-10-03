"""The decision harness must stay green. Numbers are this fixture set, not a population rate."""
from evals.harness import run_evaluation


def test_deterministic_eval_passes_without_secrets():
    report = run_evaluation()
    assert report["live_profound"] is False and report["live_model"] is False
    assert report["failed"] == 0, [r for r in report["results"] if not r["ok"]]
    assert report["unsupported_confirmations"] == 0
    assert report["temporal_leakage_failures"] == 0
    assert report["detection"]["false_positive"] == 0
    assert report["detection"]["false_negative"] == 0
    assert report["root_cause"]["top3_acceptable"] == report["root_cause"]["n"]
    assert report["domain_shift"]["scored_by_ranker"] is False
    assert report["evidence_ranker"]["available"] is True
    assert report["evidence_ranker"]["rerun_this_command"] is False
