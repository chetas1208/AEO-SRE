"""Lazy MiniLM sentence-embedding wrapper (sentence-transformers). Importing this module never imports torch."""

from __future__ import annotations

from typing import Any

EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384


class MiniLMEmbedder:
    def __init__(self, model_name: str = EMBEDDING_MODEL, device: str | None = None, batch_size: int = 128):
        self.model_name = model_name
        self.device = device
        self.batch_size = batch_size
        self._model: Any = None

    def _load(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
            self._model.max_seq_length = 256
        return self._model

    def encode(self, texts: list[str], show_progress: bool = False):
        """L2-normalised float32 embeddings, shape (len(texts), 384)."""
        return self._load().encode(
            texts, batch_size=self.batch_size, normalize_embeddings=True, convert_to_numpy=True,
            show_progress_bar=show_progress,
        ).astype("float32")

    def encode_pairs(self, claims: list[str], passages: list[str], show_progress: bool = False):
        """Encode (deduplicated) claims and passages; returns (U, V) aligned to the inputs."""
        import numpy as np

        uniq = sorted(set(claims) | set(passages))
        index = {t: i for i, t in enumerate(uniq)}
        emb = self.encode(uniq, show_progress=show_progress)
        u = emb[np.fromiter((index[c] for c in claims), dtype=np.int64, count=len(claims))]
        v = emb[np.fromiter((index[p] for p in passages), dtype=np.int64, count=len(passages))]
        return u, v
