"""Assemble model input matrices from feature dicts + (optional) embeddings. Shared by training and serving."""

from __future__ import annotations

from collections.abc import Sequence

import numpy as np

from ml.features.extract import EMBEDDING_FEATURE, LEXICAL_FEATURES

VARIANTS = ("lexical", "lex_cos", "full")


def column_names(variant: str, dim: int = 384) -> list[str]:
    if variant == "lexical":
        return list(LEXICAL_FEATURES)
    cols = [EMBEDDING_FEATURE, *LEXICAL_FEATURES]
    if variant == "lex_cos":
        return cols
    if variant == "full":
        return cols + [f"absdiff_{i}" for i in range(dim)] + [f"prod_{i}" for i in range(dim)]
    raise ValueError(f"unknown variant {variant}")


def build_matrix(
    variant: str, feats: Sequence[dict[str, float]], u: np.ndarray | None = None, v: np.ndarray | None = None
) -> np.ndarray:
    names = (EMBEDDING_FEATURE, *LEXICAL_FEATURES) if variant != "lexical" else LEXICAL_FEATURES
    base = np.array([[f.get(n, np.nan) for n in names] for f in feats], dtype=np.float32).reshape(
        len(feats), len(names)
    )
    if variant != "full":
        return base
    if u is None or v is None:
        raise ValueError("variant 'full' needs embeddings")
    return np.hstack([base, np.abs(u - v), u * v]).astype(np.float32)


def cosine_from(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Row-wise cosine for L2-normalised embeddings."""
    return np.einsum("ij,ij->i", u, v)
