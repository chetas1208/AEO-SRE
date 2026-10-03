"""Metrics for the EvidenceRanker (numpy / sklearn only)."""

from __future__ import annotations

from typing import Any

import numpy as np
from sklearn.metrics import (
    accuracy_score,
    confusion_matrix,
    f1_score,
    log_loss,
    precision_recall_fscore_support,
    roc_auc_score,
)

CLASS_NAMES = ("support", "contradiction", "insufficient")


def softmax(z: np.ndarray) -> np.ndarray:
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


def expected_calibration_error(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> float:
    edges = np.linspace(0, 1, bins + 1)
    ece, n = 0.0, len(conf)
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            ece += m.sum() / n * abs(conf[m].mean() - correct[m].mean())
    return float(ece)


def reliability_bins(conf: np.ndarray, correct: np.ndarray, bins: int = 10) -> list[dict[str, float]]:
    edges = np.linspace(0, 1, bins + 1)
    out = []
    for lo, hi in zip(edges[:-1], edges[1:], strict=True):
        m = (conf > lo) & (conf <= hi)
        if m.any():
            out.append({"lo": float(lo), "hi": float(hi), "n": int(m.sum()),
                        "mean_confidence": float(conf[m].mean()), "accuracy": float(correct[m].mean())})
    return out


def classification_report(y: np.ndarray, proba: np.ndarray) -> dict[str, Any]:
    pred = proba.argmax(axis=1)
    p, r, f, s = precision_recall_fscore_support(y, pred, labels=[0, 1, 2], zero_division=0)
    conf = proba.max(axis=1)
    correct = (pred == y).astype(float)
    onehot = np.eye(3)[y]
    return {
        "n": int(len(y)),
        "accuracy": float(accuracy_score(y, pred)),
        "balanced_accuracy": float(np.mean(r)),
        "macro_f1": float(f1_score(y, pred, average="macro", labels=[0, 1, 2], zero_division=0)),
        "per_class": {CLASS_NAMES[i]: {"precision": float(p[i]), "recall": float(r[i]), "f1": float(f[i]),
                                       "support": int(s[i])} for i in range(3)},
        "confusion_matrix": confusion_matrix(y, pred, labels=[0, 1, 2]).tolist(),
        "log_loss": float(log_loss(y, np.clip(proba, 1e-7, 1), labels=[0, 1, 2])),
        "brier": float(np.mean(np.sum((proba - onehot) ** 2, axis=1))),
        "ece": expected_calibration_error(conf, correct),
        "mean_confidence": float(conf.mean()),
    }


def binary_report(y: np.ndarray, p: np.ndarray) -> dict[str, Any]:
    pred = (p >= 0.5).astype(int)
    out: dict[str, Any] = {
        "n": int(len(y)), "base_rate": float(np.mean(y)), "accuracy": float(accuracy_score(y, pred)),
        "brier": float(np.mean((p - y) ** 2)),
        "ece": expected_calibration_error(np.maximum(p, 1 - p), (pred == y).astype(float)),
    }
    out["auc"] = float(roc_auc_score(y, p)) if len(set(y.tolist())) == 2 else None
    return out


def fit_temperature(logits: np.ndarray, y: np.ndarray) -> float:
    """Single-parameter temperature scaling minimising NLL on a held-out validation set."""
    from scipy.optimize import minimize_scalar

    def nll(t: float) -> float:
        p = softmax(logits / t)
        return float(-np.mean(np.log(np.clip(p[np.arange(len(y)), y], 1e-9, 1))))

    return float(minimize_scalar(nll, bounds=(0.2, 10.0), method="bounded").x)
