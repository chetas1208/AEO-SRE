"""Load a trained EvidenceRanker artifact and run batch inference (used by app.evidence.ranker and evaluation)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from ml.evaluation.metrics import softmax
from ml.features.matrix import build_matrix

ARTIFACT_DIR = Path(__file__).resolve().parent / "artifacts" / "evidence_ranker"
MANIFEST = "manifest.json"


@dataclass
class Artifact:
    path: Path
    manifest: dict[str, Any]
    cls_primary: Any
    cls_lexical: Any | None
    primary_variant: str
    temperature_primary: float
    temperature_lexical: float
    fresh: Any | None = None
    fresh_variant: str = "lexical"
    fresh_platt: tuple[float, float] = (1.0, 0.0)
    fresh_usable: bool = False
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", "unknown"))

    @property
    def needs_embeddings(self) -> bool:
        return self.primary_variant != "lexical"


def load_artifact(path: str | Path | None = None) -> Artifact:
    import lightgbm as lgb

    root = Path(path) if path else ARTIFACT_DIR
    manifest = json.loads((root / MANIFEST).read_text())
    cal = json.loads((root / "calibration.json").read_text())

    def booster(name: str):
        f = root / name
        return lgb.Booster(model_file=str(f)) if f.exists() else None

    return Artifact(
        path=root, manifest=manifest, cls_primary=booster("cls_primary.txt"),
        cls_lexical=booster("cls_lexical.txt"), primary_variant=manifest["primary_variant"],
        temperature_primary=float(cal["temperature_primary"]), temperature_lexical=float(cal["temperature_lexical"]),
        fresh=booster("fresh_head.txt"), fresh_variant=manifest.get("fresh_variant", "lexical"),
        fresh_platt=tuple(cal.get("fresh_platt", [1.0, 0.0])),  # type: ignore[arg-type]
        fresh_usable=bool(manifest.get("fresh_usable", False)),
    )


def predict_classes(art: Artifact, feats: list[dict], u=None, v=None, lexical_only: bool = False) -> np.ndarray:
    """Calibrated (n,3) probabilities ordered [support, contradiction, insufficient]."""
    if lexical_only or art.cls_primary is None:
        model, variant, temp = art.cls_lexical, "lexical", art.temperature_lexical
    else:
        model, variant, temp = art.cls_primary, art.primary_variant, art.temperature_primary
    raw = model.predict(build_matrix(variant, feats, u, v), raw_score=True)
    return softmax(np.asarray(raw) / temp)


def predict_fresh(art: Artifact, feats: list[dict], u=None, v=None) -> np.ndarray | None:
    if art.fresh is None:
        return None
    raw = np.asarray(art.fresh.predict(build_matrix(art.fresh_variant, feats, u, v), raw_score=True))
    a, b = art.fresh_platt
    return 1.0 / (1.0 + np.exp(-(a * raw + b)))
