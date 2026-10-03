"""SYNTHETIC labelled corpus (corpus.jsonl, hand-written for this project; not real customer data).
The corpus was authored first and then partly adjusted while building the rules (see handoff-g2.md), so the rule
numbers are an optimistic regression gate, not an out-of-sample estimate."""
from __future__ import annotations

from collections import Counter

from app.changeguard.claims import parse_claim
from app.changeguard.contradiction import evaluate_canonical

from tests.changeguard_claims.corpus_eval import (
    CLASSES,
    flagged_predict,
    load_corpus,
    metrics,
    prf,
    rules_predict,
)


def test_corpus_shape():
    rows = load_corpus()
    assert len(rows) >= 60 and all(r["synthetic"] is True for r in rows)
    assert len({r["id"] for r in rows}) == len(rows)
    counts = Counter(r["label"] for r in rows)
    assert set(counts) == set(CLASSES) and min(counts.values()) >= 10
    assert {"saml_tier", "negation", "numeric", "date", "exclusivity", "integration", "pricing",
            "adversarial", "near_miss", "paraphrase"} <= {r["category"] for r in rows}


def test_hard_conflicts_all_caught_by_rules_zero_false_allow():
    hard = [r for r in load_corpus() if r["hard"]]
    assert len(hard) >= 25
    missed = [r["id"] for r in hard if rules_predict(r)[0] != "CONFLICTING"]
    assert not missed, missed
    # and through evaluate_canonical with NO gateway/ranker: every hard conflict is a finding (no false ALLOW)
    for r in hard:
        res = evaluate_canonical([parse_claim(r["proposed"], claim_id="p")], [parse_claim(r["canonical"], claim_id="c")])
        assert any(f.type == "canonical_conflict" for f in res.findings), r["id"]
        assert res.semantic_check == "degraded"


def test_rules_only_metrics_gate_and_report(capsys):
    rows = load_corpus()
    preds = [rules_predict(r)[0] for r in rows]
    m = prf(rows, preds)
    acc = sum(p == r["label"] for p, r in zip(preds, rows, strict=True)) / len(rows)
    print("rules-only accuracy", round(acc, 3), "CONFLICTING", m)
    assert m["precision"] >= 0.95 and acc >= 0.9
    # no definitive false ALLOW outside the explicit semantic (non-hard) conflicts
    fn = [r for r, p in zip(rows, preds, strict=True) if r["label"] == "CONFLICTING" and p != "CONFLICTING"]
    assert all(not r["hard"] and r["category"] == "semantic" for r in fn)


def test_non_conflicts_do_not_raise_findings_rules_only():
    rows = [r for r in load_corpus() if r["label"] != "CONFLICTING"]
    flags = flagged_predict(rows)
    assert metrics(load_corpus(), flagged_predict(load_corpus()), 1)["fp"] == 0
    assert not any(f[0] for f in flags)
