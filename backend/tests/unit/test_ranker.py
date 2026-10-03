"""EvidenceRanker serving tests: fallback ladder, artifact loading, batch scoring, freshness behaviour.

Uses only the hand-written fixture in tests/fixtures/ranker (no network, no downloaded datasets, no torch).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from app.evidence.ranker import EvidenceRanker, RankerScore

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "ranker" / "pairs.jsonl"
LABELS = {"SUPPORTS": "support", "REFUTES": "contradiction", "NOT ENOUGH INFO": "insufficient"}
BACKEND = Path(__file__).resolve().parents[2]


def fixture_rows():
    return [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]


@pytest.fixture
def fallback() -> EvidenceRanker:
    return EvidenceRanker.load(path="/nonexistent/evidence_ranker")


def test_import_is_lazy_no_torch_or_lightgbm():
    code = (
        "import sys; import app.evidence.ranker as r; "
        "bad=[m for m in ('torch','lightgbm','sentence_transformers') if m in sys.modules]; "
        "print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""


def test_missing_artifact_gives_heuristic_fallback(fallback):
    assert fallback.available is False
    assert fallback.degraded is True
    assert fallback.version.startswith("heuristic")
    assert fallback.load_error
    s = fallback.score("Acme was founded in 1999.", "Acme was founded in 1999 in Denver.", {})
    assert isinstance(s, RankerScore)
    assert s.degraded is True and s.method == "heuristic"
    assert abs(s.support + s.contradiction + s.insufficient - 1.0) < 1e-6
    assert 0.0 <= s.freshness_risk <= 1.0


def test_heuristic_orders_clear_cases(fallback):
    sup = fallback.score("Acme was founded in 1999.", "Acme is a company founded in 1999 in Denver.")
    ref = fallback.score("Acme was founded in 2005.", "Acme is a company founded in 1999 in Denver.")
    nei = fallback.score("Acme makes toasters.", "The Denver Broncos play at Empower Field.")
    assert sup.label == "support"
    assert ref.label == "contradiction" and ref.contradiction > ref.support
    assert nei.label == "insufficient"


def test_heuristic_on_fixture_beats_chance(fallback):
    rows = fixture_rows()
    scores = fallback.score_batch([(r["claim"], r["passage"], {"title": r["title"]}) for r in rows])
    acc = sum(s.label == LABELS[r["label"]] for s, r in zip(scores, rows, strict=True)) / len(rows)
    assert acc >= 0.6, f"heuristic fixture accuracy {acc:.2f}"  # chance is 0.33


def test_batch_matches_single_and_empty_batch(fallback):
    assert fallback.score_batch([]) == []
    rows = fixture_rows()[:4]
    batch = fallback.score_batch([(r["claim"], r["passage"], None) for r in rows])
    single = [fallback.score(r["claim"], r["passage"]) for r in rows]
    assert [b.label for b in batch] == [s.label for s in single]


def test_freshness_prior_vs_known_age(fallback):
    claim, passage = "Pricing is 49 dollars.", "Current pricing is 49 dollars as of 2025."
    unknown = fallback.score(claim, passage, {})
    old = fallback.score(claim, passage, {"age_days": 1500})
    new = fallback.score(claim, passage, {"age_days": 5})
    assert unknown.freshness_known is False and old.freshness_known is True
    assert old.freshness_risk > new.freshness_risk
    assert unknown.freshness_risk < old.freshness_risk
    rev = fallback.score(claim, passage, {"age_days": 5, "revision_distance": 40})
    assert rev.freshness_risk > new.freshness_risk


def test_static_passage_less_fresh_risky_than_volatile(fallback):
    meta = {"age_days": 700}
    static = fallback.score("Berlin is a city.", "Berlin is a city in Germany.", meta)
    volatile = fallback.score("Pricing is 49 dollars.", "Current pricing is 49 dollars as of 2025.", meta)
    assert volatile.freshness_risk > static.freshness_risk


def test_long_passage_does_not_crash(fallback):
    passage = " ".join(f"Unrelated sentence {i} about weather." for i in range(400)) + " Acme was founded in 1999."
    s = fallback.score("Acme was founded in 1999.", passage)
    assert s.support > s.contradiction


@pytest.fixture
def tiny_artifact(tmp_path):
    lgb = pytest.importorskip("lightgbm")
    np = pytest.importorskip("numpy")
    from ml.features import extract_features
    from ml.features.matrix import build_matrix

    rows = fixture_rows()
    feats = [extract_features(r["claim"], r["passage"], {"title": r["title"]}) for r in rows]
    X = build_matrix("lexical", feats)
    y = np.array([["SUPPORTS", "REFUTES", "NOT ENOUGH INFO"].index(r["label"]) for r in rows])
    params = {"objective": "multiclass", "num_class": 3, "min_data_in_leaf": 1, "num_leaves": 4,
              "learning_rate": 0.3, "verbosity": -1, "num_threads": 1, "seed": 1}
    model = lgb.train(params, lgb.Dataset(X, y), 30)
    model.save_model(str(tmp_path / "cls_primary.txt"))
    model.save_model(str(tmp_path / "cls_lexical.txt"))
    (tmp_path / "calibration.json").write_text(json.dumps(
        {"temperature_primary": 1.0, "temperature_lexical": 1.0, "fresh_platt": [1.0, 0.0]}))
    (tmp_path / "manifest.json").write_text(json.dumps(
        {"version": "evidence-ranker-test-0000", "primary_variant": "lexical", "fresh_usable": False}))
    return tmp_path


def test_loads_artifact_and_scores(tiny_artifact):
    r = EvidenceRanker.load(tiny_artifact)
    assert r.available is True and r.degraded is False
    assert r.version == "evidence-ranker-test-0000"
    rows = fixture_rows()
    out = r.score_batch([(x["claim"], x["passage"], {"title": x["title"]}) for x in rows])
    assert all(isinstance(s, RankerScore) and not s.degraded and s.method == "model:lexical" for s in out)
    assert all(abs(s.support + s.contradiction + s.insufficient - 1) < 1e-6 for s in out)
    # fitted on the fixture itself -> sanity check of the model/feature plumbing only, not generalisation
    acc = sum(s.label == LABELS[x["label"]] for s, x in zip(out, rows, strict=True)) / len(rows)
    assert acc >= 0.9


def test_corrupt_artifact_falls_back(tmp_path):
    (tmp_path / "manifest.json").write_text("{not json")
    r = EvidenceRanker.load(tmp_path)
    assert r.available is False and r.degraded is True and r.load_error
    assert r.score("a b c", "a b c").degraded is True


def test_env_var_path(monkeypatch, tiny_artifact):
    monkeypatch.setenv("EVIDENCE_RANKER_PATH", str(tiny_artifact))
    assert EvidenceRanker.load().version == "evidence-ranker-test-0000"


def test_shipped_artifact_if_present():
    from ml.inference import ARTIFACT_DIR

    if not (ARTIFACT_DIR / "manifest.json").exists():
        pytest.skip("no trained artifact on this machine (run ml.training.train_evidence_ranker)")
    r = EvidenceRanker.load()
    assert r.available
    s = r.score("Berlin is the capital of Germany.", "Berlin is the capital and largest city of Germany.")
    assert abs(s.support + s.contradiction + s.insufficient - 1) < 1e-5


def test_collector_persists_ranker_provenance_and_degraded_flag():
    """V2: scores written onto Evidence.raw['ranker'] must say which method produced them and if it was degraded."""
    from app.evidence.ranker import EvidenceRanker
    from app.investigation.collector import _score_with

    out = _score_with(EvidenceRanker(), "LoopCo supports SAML SSO.", "LoopCo supports SAML SSO with Okta.", {})
    assert out is not None
    assert out["degraded"] is True and out["method"] == "heuristic" and out["ranker_available"] is False
    assert out["version"] and 0.0 <= out["support"] <= 1.0
