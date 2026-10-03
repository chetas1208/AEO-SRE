"""Train the EvidenceRanker.

    cd backend && PYTHONPATH=. .venv/bin/python -m ml.training.train_evidence_ranker --subset 40000

Pipeline: FEVER + VitaminC subsets -> MiniLM embeddings + engineered features -> LightGBM 3-class
(support / contradiction / insufficient) + temperature calibration + freshness head (VitaminC revision pairs).
Writes ml/artifacts/evidence_ranker/{cls_primary.txt, cls_lexical.txt, fresh_head.txt, calibration.json,
manifest.json, metrics.json}.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

from ml.datasets import loaders as L
from ml.evaluation import evaluate as E
from ml.evaluation.metrics import binary_report, classification_report, fit_temperature, softmax
from ml.features.embeddings import EMBEDDING_MODEL, MiniLMEmbedder
from ml.features.extract import FEATURE_NAMES, LEXICAL_FEATURES
from ml.features.matrix import build_matrix, column_names
from ml.inference import ARTIFACT_DIR
from ml.training import data as D

SEED = 13


def lgb_params(variant: str, threads: int) -> dict:
    return {
        "objective": "multiclass", "num_class": 3, "learning_rate": 0.05, "num_leaves": 63,
        "feature_fraction": 0.3 if variant == "full" else 0.8, "bagging_fraction": 0.8, "bagging_freq": 1,
        "min_data_in_leaf": 20, "lambda_l2": 1.0, "num_threads": threads, "verbosity": -1, "seed": SEED,
    }


def train_lgb(params: dict, Xtr, ytr, Xva, yva, rounds: int = 1500, patience: int = 50):
    import lightgbm as lgb

    dtr, dva = lgb.Dataset(Xtr, ytr), lgb.Dataset(Xva, yva)
    return lgb.train(params, dtr, rounds, valid_sets=[dva],
                     callbacks=[lgb.early_stopping(patience, verbose=False), lgb.log_evaluation(0)])


def leakage_report(splits: D.Splits) -> dict:
    claims = {n: {e.claim.lower() for e in getattr(splits, n)} for n in ("train", "val", "test")}
    return {
        "claim_overlap_train_test": len(claims["train"] & claims["test"]),
        "claim_overlap_train_val": len(claims["train"] & claims["val"]),
        "claim_overlap_val_test": len(claims["val"] & claims["test"]),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--subset", type=int, default=40000, help="total training examples (half FEVER, half VitaminC)")
    ap.add_argument("--val-size", type=int, default=None)
    ap.add_argument("--test-size", type=int, default=None)
    ap.add_argument("--fresh-train", type=int, default=None)
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--out", type=Path, default=ARTIFACT_DIR)
    ap.add_argument("--no-embeddings", action="store_true", help="lexical-only (skip MiniLM)")
    ap.add_argument("--device", default=None, help="torch device for MiniLM (default: auto)")
    ap.add_argument("--threads", type=int, default=16)
    args = ap.parse_args()
    t0 = time.time()
    timings: dict[str, float] = {}

    def lap(name: str) -> None:
        timings[name] = round(time.time() - t0, 1)
        print(f"[{timings[name]:>7.1f}s] {name}", flush=True)

    print("downloading / verifying raw datasets...", flush=True)
    provenance = L.download()
    lap("download")
    splits = D.build_splits(args.subset, args.seed, args.val_size, args.test_size, args.fresh_train)
    lap("build_splits")
    prov_data = D.splits_provenance(splits)
    prov_data["raw_files"] = provenance
    prov_data["leakage_check"] = leakage_report(splits)
    print(json.dumps({k: prov_data[k] for k in ("train", "val", "test", "leakage_check")}, indent=1), flush=True)

    embedder = None if args.no_embeddings else MiniLMEmbedder(device=args.device)
    fd = D.featurize_all(splits, embedder)
    lap("featurize+embed")
    tr, va, te = fd["train"], fd["val"], fd["test"]
    variants = ["lexical"] if embedder is None else ["lexical", "lex_cos", "full"]

    models, val_reports, test_reports, temps, raw_test = {}, {}, {}, {}, {}
    for variant in variants:
        Xtr, Xva, Xte = (build_matrix(variant, f.feats, f.u, f.v) for f in (tr, va, te))
        model = train_lgb(lgb_params(variant, args.threads), Xtr, tr.y, Xva, va.y)
        rv = np.asarray(model.predict(Xva, raw_score=True))
        T = fit_temperature(rv, va.y)
        rt = np.asarray(model.predict(Xte, raw_score=True))
        models[variant], temps[variant], raw_test[variant] = model, T, rt
        val_reports[variant] = classification_report(va.y, softmax(rv / T))
        test_reports[variant] = {
            "uncalibrated": classification_report(te.y, softmax(rt)),
            "calibrated": classification_report(te.y, softmax(rt / T)),
            "temperature": T, "best_iteration": model.best_iteration,
            "by_source": E.sliced(classification_report, te.y, softmax(rt / T), [e.source for e in te.examples]),
        }
        lap(f"lgbm[{variant}] best_iter={model.best_iteration} test_macroF1={test_reports[variant]['calibrated']['macro_f1']:.4f}")

    # --- baselines on the same held-out test set ---
    baselines = {"majority_class": E.majority_baseline(tr.y, te.y)}
    baselines["lr_overlap_only"], _ = E.lr_baseline(E.cols(tr.feats, E.OVERLAP_ONLY), tr.y,
                                                    E.cols(te.feats, E.OVERLAP_ONLY), te.y)
    if embedder is not None:
        baselines["lr_cosine_only"], _ = E.lr_baseline(E.cols(tr.feats, ("cosine",)), tr.y, E.cols(te.feats, ("cosine",)), te.y)
        Xl = lambda f: build_matrix("full", f.feats, f.u, f.v)  # noqa: E731
        baselines["lr_full_features_and_embeddings"], _ = E.lr_baseline(Xl(tr), tr.y, Xl(te), te.y, C=0.1)
    baselines["lr_lexical_all"], _ = E.lr_baseline(build_matrix("lexical", tr.feats), tr.y,
                                                   build_matrix("lexical", te.feats), te.y)
    lap("baselines")

    # --- choose shipped primary by validation macro-F1 (never by test) ---
    primary = max(variants, key=lambda v: val_reports[v]["macro_f1"])

    # --- freshness head ---
    ftr, fva, fte = fd["fresh_train"], fd["fresh_val"], fd["fresh_test"]
    fresh_variant = "full" if embedder is not None else "lexical"
    import lightgbm as lgb

    fparams = {"objective": "binary", "learning_rate": 0.05, "num_leaves": 31, "feature_fraction": 0.3 if fresh_variant == "full" else 0.8,
               "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 1.0, "num_threads": args.threads, "verbosity": -1,
               "seed": args.seed, "min_data_in_leaf": 30}
    Xf = [build_matrix(fresh_variant, f.feats, f.u, f.v) for f in (ftr, fva, fte)]
    fmodel = lgb.train(fparams, lgb.Dataset(Xf[0], ftr.stale), 1000, valid_sets=[lgb.Dataset(Xf[1], fva.stale)],
                       callbacks=[lgb.early_stopping(50, verbose=False)])
    from sklearn.linear_model import LogisticRegression

    rv = np.asarray(fmodel.predict(Xf[1], raw_score=True))
    platt = LogisticRegression(C=1e6).fit(rv.reshape(-1, 1), fva.stale)
    a, b = float(platt.coef_[0, 0]), float(platt.intercept_[0])
    rt = np.asarray(fmodel.predict(Xf[2], raw_score=True))
    p_te = 1 / (1 + np.exp(-(a * rt + b)))
    fresh_report = {
        "label_definition": "VitaminC real pairs: 1 = passage is the OLD revision and the verdict for the claim flips under the NEW revision; 0 = new-revision passage or no flip",
        "variant": fresh_variant, "test": binary_report(fte.stale, p_te),
        "baseline_time_sensitivity_only": binary_report(fte.stale, np.clip(E.cols(fte.feats, ("time_sensitivity",))[:, 0], 0, 1)),
        "baseline_base_rate": binary_report(fte.stale, np.full(len(fte.stale), float(ftr.stale.mean()))),
        "n_train": int(len(ftr.stale)), "n_val": int(len(fva.stale)), "best_iteration": fmodel.best_iteration,
    }
    lexf = [build_matrix("lexical", f.feats) for f in (ftr, fva, fte)]
    lm = lgb.train({**fparams, "feature_fraction": 0.8}, lgb.Dataset(lexf[0], ftr.stale), 1000,
                   valid_sets=[lgb.Dataset(lexf[1], fva.stale)], callbacks=[lgb.early_stopping(50, verbose=False)])
    fresh_report["lexical_only_head_test"] = binary_report(fte.stale, 1 / (1 + np.exp(-np.asarray(lm.predict(lexf[2], raw_score=True)))))
    auc = fresh_report["test"]["auc"] or 0.5
    fresh_usable = bool(auc >= 0.65)
    fresh_report["usable_threshold_auc"] = 0.65
    fresh_report["usable"] = fresh_usable
    lap(f"freshness head auc={auc:.4f} usable={fresh_usable}")

    # --- save artifact ---
    out = D.ensure_dir(args.out)
    models[primary].save_model(str(out / "cls_primary.txt"))
    models["lexical"].save_model(str(out / "cls_lexical.txt"))
    fmodel.save_model(str(out / "fresh_head.txt"))
    (out / "calibration.json").write_text(json.dumps({
        "temperature_primary": temps[primary], "temperature_lexical": temps["lexical"], "fresh_platt": [a, b],
    }, indent=2))
    digest = hashlib.sha256()
    for name in ("cls_primary.txt", "cls_lexical.txt", "fresh_head.txt", "calibration.json"):
        digest.update((out / name).read_bytes())
    digest.update(json.dumps(list(FEATURE_NAMES)).encode())
    sha = digest.hexdigest()
    import lightgbm
    import sklearn
    manifest = {
        "name": "evidence_ranker", "version": f"evidence-ranker-{datetime.now(UTC):%Y%m%d}-{sha[:8]}", "sha256": sha,
        "trained_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "classes": list(L.CLASS_NAMES), "primary_variant": primary, "fresh_variant": fresh_variant,
        "fresh_usable": fresh_usable, "embedding_model": EMBEDDING_MODEL if embedder else None,
        "lexical_features": list(LEXICAL_FEATURES), "feature_names": list(FEATURE_NAMES),
        "primary_columns": len(column_names(primary)), "datasets": prov_data,
        "hyperparameters": {"cls": lgb_params(primary, args.threads), "fresh": fparams},
        "libraries": {"lightgbm": lightgbm.__version__, "sklearn": sklearn.__version__, "numpy": np.__version__,
                      "python": platform.python_version()},
        "args": {k: str(v) for k, v in vars(args).items()},
    }
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2))
    lap("save_artifact")

    metrics = {
        "version": manifest["version"], "primary_variant": primary,
        "selection_rule": "highest validation macro-F1 among LightGBM variants; test never used for selection",
        "held_out_test": {"description": "class-balanced subsample of the official FEVER+VitaminC test splits",
                          "n": int(len(te.y)), "primary_calibrated": test_reports[primary]["calibrated"],
                          "primary_by_source": test_reports[primary]["by_source"]},
        "variants": {v: {"validation": val_reports[v], "test": test_reports[v]} for v in variants},
        "baselines_on_test": baselines, "freshness_head": fresh_report,
        "runtime_seconds": {"stages_cumulative": timings, "total": round(time.time() - t0, 1)},
        "hardware": {"device": args.device or "auto (cpu; cuda driver too old for installed torch)"},
        "caveats": [
            "Trained and evaluated on Wikipedia-derived claim/evidence pairs; web-page evidence is out of distribution.",
            "Freshness labels are derived from VitaminC revision-pair structure, not human stale/fresh annotation.",
            "source_age / owned / revision_distance are not learned (no counterpart in training data).",
        ],
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2))
    lap("done")
    print(json.dumps({"primary": primary, "test_calibrated": {k: metrics["held_out_test"]["primary_calibrated"][k]
                                                              for k in ("accuracy", "macro_f1", "ece")}}, indent=1))


if __name__ == "__main__":
    main()
