"""Split construction and featurisation for EvidenceRanker training / evaluation."""

from __future__ import annotations

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from ml.datasets import loaders as L
from ml.features.embeddings import EMBEDDING_MODEL, MiniLMEmbedder
from ml.features.extract import extract_features

CACHE_DIR = L.RAW_DIR / "cache"


@dataclass
class Splits:
    train: list[L.Example]
    val: list[L.Example]
    test: list[L.Example]
    fresh_train: list[L.Example]
    fresh_val: list[L.Example]
    fresh_test: list[L.Example]
    config: dict = field(default_factory=dict)

    def all_examples(self) -> list[L.Example]:
        seen: dict[str, L.Example] = {}
        for part in (self.train, self.val, self.test, self.fresh_train, self.fresh_val, self.fresh_test):
            for e in part:
                seen.setdefault(e.uid, e)
        return list(seen.values())


def _balanced_two_source(fever: list[L.Example], vit: list[L.Example], n: int, seed: int) -> list[L.Example]:
    """n examples, half FEVER half VitaminC, class-balanced within FEVER and (label, revision_type) in VitaminC."""
    out = L.stratified_subset(fever, n // 2, seed, key=lambda e: e.label)
    out += L.stratified_subset(vit, n - n // 2, seed, key=L.vitaminc_stratum)
    return out


def build_splits(subset: int = 40000, seed: int = 13, val_size: int | None = None, test_size: int | None = None,
                 fresh_train: int | None = None) -> Splits:
    val_size = val_size or max(600, int(subset * 0.15))
    test_size = test_size or max(1200, int(subset * 0.30))
    fresh_train_n = fresh_train or max(1000, int(subset * 0.3))
    data = {(s, sp): (L.load_fever if s == "fever" else L.load_vitaminc)(sp)
            for s in ("fever", "vitaminc") for sp in ("train", "val", "test")}
    parts = {}
    for sp, n, sd in (("train", subset, seed), ("val", val_size, seed + 1), ("test", test_size, seed + 2)):
        parts[sp] = _balanced_two_source(data[("fever", sp)], data[("vitaminc", sp)], n, sd)

    def fresh(sp: str, n: int, sd: int) -> list[L.Example]:
        pool = [e for e in data[("vitaminc", sp)] if e.stale is not None]
        return L.stratified_subset(pool, n, sd, key=lambda e: (e.stale, e.label))

    return Splits(
        train=parts["train"], val=parts["val"], test=parts["test"],
        fresh_train=fresh("train", fresh_train_n, seed + 3), fresh_val=fresh("val", max(800, fresh_train_n // 6), seed + 4),
        fresh_test=fresh("test", max(1500, fresh_train_n // 3), seed + 5),
        config={"subset": subset, "val_size": val_size, "test_size": test_size, "fresh_train": fresh_train_n,
                "seed": seed, "pool_sizes": {f"{s}/{sp}": len(v) for (s, sp), v in data.items()}},
    )


def _feat_chunk(rows: list[tuple[str, str, str]]) -> list[dict[str, float]]:
    return [extract_features(c, p, {"title": t}) for c, p, t in rows]


def lexical_features(examples: list[L.Example], workers: int | None = None) -> list[dict[str, float]]:
    rows = [(e.claim, e.passage, e.title) for e in examples]
    if len(rows) < 2000:
        return _feat_chunk(rows)
    workers = workers or min(16, os.cpu_count() or 4)
    chunks = [rows[i:i + 1000] for i in range(0, len(rows), 1000)]
    with ProcessPoolExecutor(workers) as ex:
        return [f for part in ex.map(_feat_chunk, chunks) for f in part]


def embed_examples(examples: list[L.Example], embedder: MiniLMEmbedder, cache: bool = True):
    """Return (u, v) aligned with `examples`; disk-cached per example-uid set."""
    key = hashlib.sha1(("|".join(sorted(e.uid for e in examples)) + EMBEDDING_MODEL).encode()).hexdigest()[:16]
    path = CACHE_DIR / f"emb_{key}.npz"
    uids = [e.uid for e in examples]
    if cache and path.exists():
        z = np.load(path, allow_pickle=False)
        order = {u: i for i, u in enumerate(z["uids"].tolist())}
        idx = np.array([order[u] for u in uids])
        return z["u"][idx], z["v"][idx]
    u, v = embedder.encode_pairs([e.claim for e in examples], [e.passage for e in examples])
    if cache:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        np.savez(path, uids=np.array(uids), u=u, v=v)
    return u, v


@dataclass
class Featurized:
    examples: list[L.Example]
    feats: list[dict[str, float]]
    u: np.ndarray | None
    v: np.ndarray | None
    y: np.ndarray
    stale: np.ndarray  # -1 where unlabeled


def featurize_all(splits: Splits, embedder: MiniLMEmbedder | None) -> dict[str, Featurized]:
    """Featurise every split once (unique examples), then slice per split."""
    from ml.features.matrix import cosine_from

    allx = splits.all_examples()
    feats = lexical_features(allx)
    if embedder is not None:
        u, v = embed_examples(allx, embedder)
        cos = cosine_from(u, v)
        for f, c in zip(feats, cos, strict=True):
            f["cosine"] = float(c)
    else:
        u = v = None
    pos = {e.uid: i for i, e in enumerate(allx)}
    out: dict[str, Featurized] = {}
    for name in ("train", "val", "test", "fresh_train", "fresh_val", "fresh_test"):
        exs = getattr(splits, name)
        idx = [pos[e.uid] for e in exs]
        out[name] = Featurized(
            exs, [feats[i] for i in idx], None if u is None else u[idx], None if v is None else v[idx],
            np.array([e.label for e in exs]), np.array([-1 if e.stale is None else e.stale for e in exs]),
        )
    return out


def splits_provenance(splits: Splits) -> dict:
    return {
        "config": splits.config,
        "train": L.summarize(splits.train), "val": L.summarize(splits.val), "test": L.summarize(splits.test),
        "fresh_train": L.summarize(splits.fresh_train), "fresh_val": L.summarize(splits.fresh_val),
        "fresh_test": L.summarize(splits.fresh_test),
        "split_policy": (
            "train/val/test come from the datasets' own official, disjoint splits (VitaminC train/dev/test; "
            "copenlu FEVER train/valid/test); each is subsampled class-balanced; no claim appears in two splits "
            "by construction of the official splits"
        ),
    }


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p
