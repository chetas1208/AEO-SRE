"""EvidenceRanker: claim + passage (+ meta) -> {support, contradiction, insufficient, freshness_risk}.

Serving wrapper around the artifact trained by `ml/training/train_evidence_ranker.py`. Degradation ladder:

  1. primary model (MiniLM embeddings + engineered features -> LightGBM)        degraded=False
  2. lexical LightGBM (engineered features only; used if sentence-transformers / torch is unavailable)
                                                                                    degraded=True
  3. feature-only rules (no artifact / no lightgbm)                                 degraded=True, available=False

Importing this module never imports torch, lightgbm, or sentence-transformers (all lazy).
"""

from __future__ import annotations

import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

AGE_HALF_SCALE_DAYS = 730.0  # age_risk = 1 - exp(-age/730): ~63% at two years
REVISION_SCALE = 10.0
LONG_PASSAGE_CHARS = 1200
ENV_PATH = "EVIDENCE_RANKER_PATH"


@dataclass(frozen=True)
class RankerScore:
    support: float
    contradiction: float
    insufficient: float
    freshness_risk: float
    label: str  # argmax of the three class probabilities
    confidence: float
    degraded: bool
    method: str  # model:<variant> | heuristic
    version: str
    freshness_known: bool = False  # False => freshness_risk is a prior (no age / revision info supplied)
    components: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _isnan(x: float) -> bool:
    return x != x


class EvidenceRanker:
    def __init__(self) -> None:
        self._artifact: Any = None
        self._embedder: Any = None
        self._embedder_failed = False
        self.load_error: str | None = None
        self.path: Path | None = None

    # ---------------------------------------------------------------- loading
    @classmethod
    def load(cls, path: str | Path | None = None) -> EvidenceRanker:
        """Never raises: on any failure the instance serves the heuristic fallback with `load_error` set."""
        self = cls()
        raw = path or os.environ.get(ENV_PATH)
        target = Path(raw) if raw else None
        try:
            from ml.inference import load_artifact

            art = load_artifact(target)
            if art.cls_primary is None and art.cls_lexical is None:
                raise FileNotFoundError("artifact has no classifier")
            self._artifact, self.path = art, art.path
        except Exception as exc:  # missing artifact, lightgbm absent, corrupt file ...
            self.load_error = f"{type(exc).__name__}: {exc}"
            log.info("evidence_ranker.heuristic_fallback", reason=self.load_error)
        return self

    @property
    def available(self) -> bool:
        """True when a trained model is loaded (even in lexical-only degraded mode)."""
        return self._artifact is not None

    @property
    def version(self) -> str:
        if self._artifact is not None:
            return self._artifact.version
        from ml.heuristic import HEURISTIC_VERSION

        return HEURISTIC_VERSION

    @property
    def degraded(self) -> bool:
        if self._artifact is None:
            return True
        if self._artifact.needs_embeddings:
            return self._get_embedder() is None
        return False

    @property
    def metrics(self) -> dict[str, Any] | None:
        if self._artifact is None:
            return None
        import json

        f = self._artifact.path / "metrics.json"
        return json.loads(f.read_text()) if f.exists() else None

    def status(self) -> dict[str, Any]:
        return {
            "available": self.available, "degraded": self.degraded, "version": self.version,
            "load_error": self.load_error, "path": str(self.path) if self.path else None,
            "method": self._method_name(),
        }

    def _method_name(self) -> str:
        if self._artifact is None:
            return "heuristic"
        if self._artifact.needs_embeddings and self._get_embedder() is None:
            return "model:lexical"
        return f"model:{self._artifact.primary_variant}"

    def _get_embedder(self):
        if self._embedder is None and not self._embedder_failed:
            try:
                from ml.features.embeddings import MiniLMEmbedder

                emb = MiniLMEmbedder(batch_size=32)
                emb.encode(["warmup"])
                self._embedder = emb
            except Exception as exc:  # torch / sentence-transformers missing, or weights not cached offline
                self._embedder_failed = True
                self.load_error = self.load_error or f"embedder unavailable: {type(exc).__name__}: {exc}"
                log.info("evidence_ranker.embedder_unavailable", reason=str(exc))
        return self._embedder

    # ---------------------------------------------------------------- scoring
    def score(self, claim: str, passage: str, meta: dict[str, Any] | None = None) -> RankerScore:
        return self.score_batch([(claim, passage, meta or {})])[0]

    def score_batch(self, items: list[tuple[str, str, dict[str, Any] | None]]) -> list[RankerScore]:
        if not items:
            return []
        from ml.features import extract_features, select_window

        prepared = []
        for claim, passage, meta in items:
            meta = dict(meta or {})
            window = select_window(claim, passage, LONG_PASSAGE_CHARS)
            prepared.append((claim, window, meta))
        feats = [extract_features(c, p, m) for c, p, m in prepared]

        embedder = self._get_embedder() if (self._artifact and self._artifact.needs_embeddings) else None
        u = v = None
        if embedder is not None:
            from ml.features.matrix import cosine_from

            u, v = embedder.encode_pairs([c for c, _, _ in prepared], [p for _, p, _ in prepared])
            for f, cos in zip(feats, cosine_from(u, v), strict=True):
                f["cosine"] = float(cos)

        probs, fresh_head, method, degraded = self._class_probs(feats, u, v, embedder)
        return [
            self._assemble(f, tuple(map(float, probs[i])), None if fresh_head is None else float(fresh_head[i]),
                           method, degraded)
            for i, f in enumerate(feats)
        ]

    def _class_probs(self, feats, u, v, embedder):
        art = self._artifact
        if art is None:
            from ml.heuristic import heuristic_class_probs

            return [heuristic_class_probs(f) for f in feats], None, "heuristic", True
        from ml.inference import predict_classes, predict_fresh

        if art.needs_embeddings and embedder is None:
            if art.cls_lexical is None:
                from ml.heuristic import heuristic_class_probs

                return [heuristic_class_probs(f) for f in feats], None, "heuristic", True
            probs = predict_classes(art, feats, lexical_only=True)
            fresh = predict_fresh(art, feats) if art.fresh_variant == "lexical" and art.fresh_usable else None
            return probs, fresh, "model:lexical", True
        probs = predict_classes(art, feats, u, v)
        fresh = predict_fresh(art, feats, u, v) if art.fresh_usable else None
        return probs, fresh, f"model:{art.primary_variant}", False

    def _freshness(self, feats: dict[str, float], head: float | None) -> tuple[float, bool, dict[str, Any]]:
        """freshness_risk in [0,1].

        Known age/revision info: age_risk = 1 - exp(-age_days/730), revision_risk = 1 - exp(-rev_distance/10),
        scaled by how time-sensitive the passage looks (numbers, dates, 'currently'/'pricing'...). The learned
        VitaminC head (only used when its held-out AUC cleared the usability threshold) is combined by noisy-OR.
        No age/revision info: a low prior driven by time-sensitivity only, flagged `freshness_known=False`.
        """
        vol = feats.get("time_sensitivity", 0.0)
        age_log, rev_log = feats.get("source_age_log", float("nan")), feats.get("revision_distance_log", float("nan"))
        comps: dict[str, Any] = {"time_sensitivity": round(vol, 4)}
        known = not (_isnan(age_log) and _isnan(rev_log))
        if known:
            age_risk = 0.0 if _isnan(age_log) else 1 - math.exp(-math.expm1(age_log) / AGE_HALF_SCALE_DAYS)
            rev_risk = 0.0 if _isnan(rev_log) else 1 - math.exp(-math.expm1(rev_log) / REVISION_SCALE)
            base = max(age_risk, rev_risk) * (0.5 + 0.5 * vol)
            comps.update(age_risk=round(age_risk, 4), revision_risk=round(rev_risk, 4))
        else:
            base = 0.1 + 0.2 * vol
        risk = base
        if head is not None:
            comps["learned_head"] = round(head, 4)
            risk = 1 - (1 - base) * (1 - head)
        comps["heuristic_component"] = round(base, 4)
        return float(min(max(risk, 0.0), 1.0)), known, comps

    def _assemble(self, feats, probs, head, method, degraded) -> RankerScore:
        s, c, i = probs
        risk, known, comps = self._freshness(feats, head)
        labels = ("support", "contradiction", "insufficient")
        best = max(range(3), key=lambda k: probs[k])
        keep = ("cosine", "claim_coverage", "ent_overlap", "num_conflict", "comp_violated", "year_conflict",
                "neg_mismatch", "antonym_conflict", "title_sim")
        comps["signals"] = {k: (None if _isnan(feats.get(k, float("nan"))) else round(feats[k], 4)) for k in keep}
        return RankerScore(
            support=s, contradiction=c, insufficient=i, freshness_risk=risk, label=labels[best],
            confidence=probs[best], degraded=degraded, method=method, version=self.version,
            freshness_known=known, components=comps,
        )
