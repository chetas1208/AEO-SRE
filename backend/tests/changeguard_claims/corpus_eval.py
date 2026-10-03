"""Corpus evaluation helpers (SYNTHETIC labelled corpus, see corpus.jsonl)."""
from __future__ import annotations

import json
from pathlib import Path

from app.changeguard.claims import parse_claim
from app.changeguard.contradiction import compare_claims

CORPUS = Path(__file__).with_name("corpus.jsonl")
CLASSES = ("DUPLICATE", "COMPATIBLE", "DEPENDENT", "CONFLICTING", "UNRELATED")


def load_corpus() -> list[dict]:
    return [json.loads(line) for line in CORPUS.read_text().splitlines() if line.strip()]


def rules_predict(row: dict) -> tuple[str, bool]:
    rel = compare_claims(parse_claim(row["proposed"], claim_id="p"), parse_claim(row["canonical"], claim_id="c"))
    return rel.relation, rel.needs_semantic


def prf(rows: list[dict], preds: list[str], cls: str = "CONFLICTING") -> dict[str, float]:
    tp = sum(1 for r, p in zip(rows, preds, strict=True) if p == cls and r["label"] == cls)
    fp = sum(1 for r, p in zip(rows, preds, strict=True) if p == cls and r["label"] != cls)
    fn = sum(1 for r, p in zip(rows, preds, strict=True) if p != cls and r["label"] == cls)
    return {"precision": tp / (tp + fp) if tp + fp else 1.0, "recall": tp / (tp + fn) if tp + fn else 1.0,
            "tp": tp, "fp": fp, "fn": fn}


def flagged_predict(rows: list[dict], *, ranker=None, gateway=None) -> list[tuple[bool, bool]]:
    """(strict_conflict, any_concern) per row using evaluate_canonical on a single pair."""
    from app.changeguard.contradiction import evaluate_canonical

    out = []
    for r in rows:
        res = evaluate_canonical([parse_claim(r["proposed"], claim_id="p")],
                                 [parse_claim(r["canonical"], claim_id="c")], ranker=ranker, gateway=gateway)
        out.append((any(f.type == "canonical_conflict" for f in res.findings), bool(res.findings)))
    return out


def metrics(rows: list[dict], flags: list[tuple[bool, bool]], which: int) -> dict[str, float]:
    tp = sum(1 for r, f in zip(rows, flags, strict=True) if f[which] and r["label"] == "CONFLICTING")
    fp = sum(1 for r, f in zip(rows, flags, strict=True) if f[which] and r["label"] != "CONFLICTING")
    fn = sum(1 for r, f in zip(rows, flags, strict=True) if not f[which] and r["label"] == "CONFLICTING")
    return {"tp": tp, "fp": fp, "fn": fn, "precision": round(tp / (tp + fp), 3) if tp + fp else 1.0,
            "recall": round(tp / (tp + fn), 3) if tp + fn else 1.0}


def main() -> None:  # python -m tests.changeguard_claims.corpus_eval  (real ranker if available)
    from collections import Counter

    rows = load_corpus()
    print("rows", len(rows), dict(Counter(r["label"] for r in rows)))
    preds = [rules_predict(r)[0] for r in rows]
    acc = sum(p == r["label"] for p, r in zip(preds, rows, strict=True)) / len(rows)
    print("rules-only 5-class accuracy", round(acc, 3), "conflicting", prf(rows, preds))
    for cls in CLASSES:
        print(" ", cls, prf(rows, preds, cls))
    hard = [r for r in rows if r["hard"]]
    print("hard conflicts caught by rules:", sum(rules_predict(r)[0] == "CONFLICTING" for r in hard), "/", len(hard))
    from app.evidence.ranker import EvidenceRanker

    ranker = EvidenceRanker.load()
    print("ranker", ranker.status()["method"], "degraded", ranker.degraded)
    flags = flagged_predict(rows, ranker=ranker)
    print("rules+ranker strict-conflict", metrics(rows, flags, 0), "any-concern", metrics(rows, flags, 1))
    sc = ranker.score_batch([(r["proposed"], r["canonical"], {}) for r in rows])
    rf = [(x.label == "contradiction", x.label == "contradiction") for x in sc]
    print("RAW ranker alone (every pair) conflict-vs-rest", metrics(rows, rf, 0))
    sem = [r for r in rows if r["category"] == "semantic"]
    print("ranker label on semantic (non-hard) conflicts:", [(r["id"], x.label, round(x.contradiction, 2)) for r, x in
          zip(rows, sc, strict=True) if r["category"] == "semantic"], len(sem))
    print("rules-only  any-concern", metrics(rows, flagged_predict(rows), 1))


if __name__ == "__main__":
    main()
