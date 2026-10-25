"""Cross-encoder reranking with BAAI/bge-reranker-v2-m3 (local, CPU)."""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Callable

from src.db import RetrievedChunk

if TYPE_CHECKING:
    from sentence_transformers import CrossEncoder

RERANK_MODEL = "BAAI/bge-reranker-v2-m3"
MAX_LENGTH = 512  # query + chunk tokens; longer pairs are truncated

Scorer = Callable[[str, list[str]], list[float]]


@lru_cache(maxsize=1)
def _model() -> CrossEncoder:
    from sentence_transformers import CrossEncoder  # slow import; load on first use

    return CrossEncoder(RERANK_MODEL, max_length=MAX_LENGTH)


def score_pairs(query: str, texts: list[str]) -> list[float]:
    """Relevance score of each text for the query (higher is better)."""
    if not texts:
        return []
    return [float(s) for s in _model().predict([(query, t) for t in texts])]


def rerank(query: str, chunks: list[RetrievedChunk], scorer: Scorer = score_pairs) -> list[tuple[RetrievedChunk, float]]:
    """Chunks sorted by cross-encoder score, best first; ties keep the input (fused) order."""
    scores = scorer(query, [c.text for c in chunks])
    order = sorted(range(len(chunks)), key=lambda i: (-scores[i], i))
    return [(chunks[i], scores[i]) for i in order]
