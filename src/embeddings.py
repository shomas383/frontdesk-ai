"""Local embeddings with BAAI/bge-m3 (dense, normalized, 1024 dims)."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING

import numpy as np

from src.config import EMBED_DIM, get_settings

if TYPE_CHECKING:
    from sentence_transformers import SentenceTransformer


@lru_cache(maxsize=1)
def _model() -> SentenceTransformer:
    from sentence_transformers import SentenceTransformer  # slow import; load on first use

    return SentenceTransformer(get_settings().embed_model)


def embed(texts: list[str]) -> np.ndarray:
    vectors = _model().encode(texts, normalize_embeddings=True, convert_to_numpy=True)
    if vectors.shape[1] != EMBED_DIM:
        raise ValueError(f"Expected {EMBED_DIM}-dim embeddings, got {vectors.shape[1]}")
    return vectors.astype(np.float32)


def embed_one(text: str) -> np.ndarray:
    return embed([text])[0]
