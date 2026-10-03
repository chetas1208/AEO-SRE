"""Held-out evaluation for the EvidenceRanker.

`python -m ml.evaluation.evaluate [--artifact DIR]` re-evaluates a shipped artifact on its (deterministic,
seed-recorded) held-out test sample and writes `metrics_reeval.json` next to it. Training calls the same
functions (`evaluate_variants`, `evaluate_freshness`) to build the comparison table.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import numpy as np

from ml.evaluation.metrics import binary_report, classification_report

OVERLAP_ONLY = ("tok_jaccard", "claim_coverage", "passage_coverage", "bigram_coverage", "unmatched_claim_tokens",
                "claim_len_log", "passage_len_log", "len_ratio")


def sliced(report_fn, y, proba, sources, by="source") -> dict[str, Any]:
    out = {}
    for s in sorted(set(sources)):
        m = np.array([x == s for x in sources])
        out[s] = report_fn(y[m], proba[m])
    return out


def majority_baseline(y_train: np.ndarray, y_test: np.ndarray) -> dict[str, Any]:
    prior = np.bincount(y_train, minlength=3) / len(y_train)
    return classification_report(y_test, np.tile(prior, (len(y_test), 1)))


def lr_baseline(train_X: np.ndarray, y_train: np.ndarray, test_X: np.ndarray, y_test: np.ndarray, C: float = 1.0):
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import LogisticRegression
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler

    pipe = make_pipeline(SimpleImputer(strategy="median", keep_empty_features=True), StandardScaler(),
                         LogisticRegression(C=C, max_iter=500))
    pipe.fit(train_X, y_train)
    proba = pipe.predict_proba(test_X)
    return classification_report(y_test, proba), proba


def cols(feats: list[dict], names: tuple[str, ...]) -> np.ndarray:
    return np.array([[f.get(n, np.nan) for n in names] for f in feats], dtype=np.float32)


def evaluate_freshness(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    return binary_report(y, p)


def reevaluate(artifact_dir: Path | None = None) -> dict[str, Any]:
    from ml.inference import load_artifact, predict_classes, predict_fresh
    from ml.training.data import build_splits, featurize_all

    art = load_artifact(artifact_dir)
    cfg = art.manifest["datasets"]["config"]
    splits = build_splits(cfg["subset"], cfg["seed"], cfg["val_size"], cfg["test_size"], cfg["fresh_train"])
    embedder = None
    if art.needs_embeddings:
        from ml.features.embeddings import MiniLMEmbedder
        embedder = MiniLMEmbedder()
    fd = featurize_all(splits, embedder)
    t = fd["test"]
    out: dict[str, Any] = {"reevaluated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "artifact": art.version}
    proba = predict_classes(art, t.feats, t.u, t.v)
    srcs = [e.source for e in t.examples]
    out["primary"] = {"overall": classification_report(t.y, proba),
                      "by_source": sliced(classification_report, t.y, proba, srcs)}
    if art.cls_lexical is not None:
        pl = predict_classes(art, t.feats, t.u, t.v, lexical_only=True)
        out["lexical_fallback"] = {"overall": classification_report(t.y, pl)}
    from ml.heuristic import heuristic_class_probs

    ph = np.array([heuristic_class_probs(f) for f in t.feats])
    out["feature_rule_heuristic"] = {"overall": classification_report(t.y, ph),
                                     "by_source": sliced(classification_report, t.y, ph, srcs)}
    ft = fd["fresh_test"]
    pf = predict_fresh(art, ft.feats, ft.u, ft.v)
    if pf is not None:
        out["freshness_head"] = evaluate_freshness(ft.stale, pf)
    return out


def main() -> None:
    from ml.inference import ARTIFACT_DIR

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--artifact", type=Path, default=ARTIFACT_DIR)
    args = ap.parse_args()
    res = reevaluate(args.artifact)
    target = args.artifact / "metrics_reeval.json"
    target.write_text(json.dumps(res, indent=2))
    mf = args.artifact / "metrics.json"
    if mf.exists():  # attach the rule-heuristic baseline + reevaluation agreement to the training metrics
        m = json.loads(mf.read_text())
        m.setdefault("baselines_on_test", {})["feature_rule_heuristic"] = res["feature_rule_heuristic"]["overall"]
        m["reevaluation"] = {"primary_macro_f1": res["primary"]["overall"]["macro_f1"],
                             "primary_accuracy": res["primary"]["overall"]["accuracy"],
                             "note": "independent reload of the saved artifact on the regenerated test split"}
        mf.write_text(json.dumps(m, indent=2))
    print(json.dumps({k: (v["overall"] if isinstance(v, dict) and "overall" in v else v) for k, v in res.items()}
                     , indent=2)[:4000])
    print("written", target)


if __name__ == "__main__":
    main()
