"""Unit tests for EvidenceRanker feature extraction (pure functions; no torch / lightgbm / network)."""

import math
from datetime import UTC, datetime, timedelta

from ml.features import FEATURE_NAMES, LEXICAL_FEATURES, extract_features, feature_vector, select_window
from ml.features import text as T
from ml.features.extract import is_owned, source_age_days


def f(claim, passage, meta=None, cosine=None):
    return extract_features(claim, passage, meta, cosine)


def test_feature_dict_has_every_declared_name():
    feats = f("A is B.", "A is B.")
    assert set(FEATURE_NAMES) <= set(feats)
    assert len(feature_vector(feats, LEXICAL_FEATURES)) == len(LEXICAL_FEATURES)


def test_token_overlap_identical_vs_disjoint():
    same = f("Acme makes rockets", "Acme makes rockets")
    diff = f("Acme makes rockets", "Penguins eat fish in Antarctica")
    assert same["claim_coverage"] == 1.0 and same["tok_jaccard"] == 1.0
    assert diff["claim_coverage"] == 0.0 and diff["unmatched_claim_tokens"] >= 2


def test_cosine_passthrough_and_missing_is_nan():
    assert f("a b", "a b", cosine=0.73)["cosine"] == 0.73
    assert math.isnan(f("a b", "a b")["cosine"])


def test_numbers_scale_and_commas():
    assert T.extract_numbers("sales of 1,200 units") == [1200.0]
    assert T.extract_numbers("revenue of 2.5 million") == [2_500_000.0]
    assert T.extract_numbers("founded in 1999") == []  # year-like bare ints are dates, not quantities


def test_numeric_mismatch_detected():
    match = f("The plan costs 49 dollars.", "The plan costs 49 dollars per month.")
    clash = f("The plan costs 20 dollars.", "The plan costs 49 dollars per month.")
    assert match["num_exact_frac"] == 1.0 and match["num_conflict"] == 0.0
    assert clash["num_exact_frac"] == 0.0 and clash["num_conflict"] == 1.0


def test_comparator_satisfied_and_violated():
    ok = f("It costs more than 40 dollars.", "It costs 49 dollars.")
    bad = f("It costs less than 40 dollars.", "It costs 49 dollars.")
    assert ok["comp_satisfied"] == 1.0 and ok["comp_violated"] == 0.0
    assert bad["comp_violated"] == 1.0 and bad["comp_satisfied"] == 0.0


def test_date_mismatch_years_and_months():
    assert f("Founded in 2005.", "It was founded in 1999.")["year_conflict"] == 1.0
    assert f("Founded in 1999.", "It was founded in 1999.")["year_conflict"] == 0.0
    assert f("Launched in March 2021.", "Launched in October 2021.")["month_conflict"] == 1.0
    assert T.extract_months("it may rain") == set()  # modal 'may' is not a month


def test_negation_mismatch():
    assert f("It is not in Japan.", "It is located in Japan.")["neg_mismatch"] == 1.0
    assert f("It is in Japan.", "It is located in Japan.")["neg_mismatch"] == 0.0
    assert f("It isn't in Japan.", "It is located in Japan.")["neg_claim"] == 1.0


def test_entity_overlap():
    hit = f("Marie Curie won a prize.", "Marie Curie was a physicist who won a prize.")
    miss = f("Marie Curie won a prize.", "Isaac Newton described gravity.")
    assert hit["ent_overlap"] == 1.0
    assert miss["ent_overlap"] == 0.0


def test_title_similarity_needs_meta():
    assert math.isnan(f("Fuji is tall", "x")["title_sim"])
    assert f("Mount Fuji is tall", "x", {"title": "Mount Fuji"})["title_in_claim"] == 1.0


def test_antonym_conflict():
    assert f("Sales increased.", "Sales decreased last year.")["antonym_conflict"] >= 1.0


def test_source_age_and_owned_meta():
    now = datetime(2026, 10, 1, tzinfo=UTC)
    assert source_age_days({"age_days": 30}) == 30
    assert abs(source_age_days({"published_at": (now - timedelta(days=100)).isoformat()}, now) - 100) < 1e-6
    assert math.isnan(source_age_days({}))
    assert is_owned({"owned": True}) == 1.0 and is_owned({"owned": False}) == 0.0
    assert is_owned({"url": "https://www.acme.com/pricing", "owned_domains": ["acme.com"]}) == 1.0
    assert is_owned({"url": "https://reddit.com/r/x", "owned_domains": ["acme.com"]}) == 0.0
    assert math.isnan(is_owned({}))
    feats = f("a", "b", {"age_days": 365, "owned": True, "revision_distance": 3})
    assert abs(feats["source_age_log"] - math.log1p(365)) < 1e-9
    assert feats["is_owned"] == 1.0 and abs(feats["revision_distance_log"] - math.log1p(3)) < 1e-9


def test_time_sensitivity_rises_with_dates_and_cues():
    static = f("Berlin is a city.", "Berlin is a city in Germany.")
    volatile = f("Pricing is currently 49 dollars.", "Current pricing is 49 dollars as of 2025.")
    assert volatile["time_sensitivity"] > static["time_sensitivity"]


def test_select_window_prefers_relevant_sentences():
    filler = " ".join(f"Filler sentence number {i} about nothing." for i in range(80))
    passage = filler + " The Plus plan costs 49 dollars per month. It is billed annually. " + filler
    out = select_window("The Plus plan costs 49 dollars", passage, max_chars=300)
    assert "Plus plan costs 49" in out and len(out) <= 300


def test_empty_inputs_do_not_crash():
    feats = f("", "")
    assert feats["claim_coverage"] == 0.0
