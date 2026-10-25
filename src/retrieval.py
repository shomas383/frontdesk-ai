"""Retrieval strategies: dense (pgvector), hybrid (dense + BM25 via reciprocal rank fusion).

Every mode returns the top-k chunks passed to the LLM plus diagnostics (ranks per retriever and
the fused candidate pool), so a miss can be traced to the pool or to the final ranking.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

import psycopg

from src import db
from src.config import TOP_K
from src.db import RetrievedChunk
from src.embeddings import embed_one
from src.lexical import BM25, TOKENIZER_NAME, tokenize

RETRIEVAL_MODES = ("dense", "hybrid")
CANDIDATES = 20  # per retriever, and the size of the fused candidate pool
RRF_K = 60  # standard reciprocal-rank-fusion constant


@dataclass
class Retrieval:
    chunks: list[RetrievedChunk]  # top-k, in the order shown to the LLM; score is mode-specific
    details: list[dict[str, Any]]  # per chunk: chunk_id, doc_id, score, dense_rank, keyword_rank
    candidate_chunk_ids: list[str] = field(default_factory=list)


def retrieval_config(mode: str) -> dict[str, Any]:
    """What the run config records about retrieval."""
    config: dict[str, Any] = {"retrieval": mode}
    if mode != "dense":
        config.update(candidates=CANDIDATES, rrf_k=RRF_K, tokenizer=TOKENIZER_NAME)
    return config


def rrf_fuse(rankings: list[list[str]], k: int = RRF_K) -> list[tuple[str, float]]:
    """Reciprocal rank fusion: score = sum of 1/(k + rank). Ties break by rank in the first
    ranking (dense), then chunk_id, so results are deterministic."""
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, cid in enumerate(ranking, 1):
            scores[cid] = scores.get(cid, 0.0) + 1 / (k + rank)
    first = {cid: i for i, cid in enumerate(rankings[0])} if rankings else {}
    return sorted(scores.items(), key=lambda x: (-x[1], first.get(x[0], len(first)), x[0]))


@dataclass
class _KeywordIndex:
    fingerprint: tuple[object, ...]
    chunks: list[RetrievedChunk]
    bm25: BM25


_indexes: dict[str, _KeywordIndex] = {}


def keyword_index(conn: psycopg.Connection, tenant_id: str) -> _KeywordIndex:
    """BM25 over the tenant's chunk texts, rebuilt when the stored chunks change."""
    fingerprint = db.chunks_fingerprint(conn, tenant_id)
    index = _indexes.get(tenant_id)
    if index is None or index.fingerprint != fingerprint:
        chunks = db.all_chunks(conn, tenant_id)
        index = _KeywordIndex(fingerprint, chunks, BM25([tokenize(c.text) for c in chunks]))
        _indexes[tenant_id] = index
    return index


def _ranks(ids: list[str]) -> dict[str, int]:
    return {cid: i for i, cid in enumerate(ids, 1)}


def _details(chunks: list[RetrievedChunk], dense: dict[str, int], keyword: dict[str, int]) -> list[dict[str, Any]]:
    return [
        {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "score": round(c.score, 4),
         "dense_rank": dense.get(c.chunk_id), "keyword_rank": keyword.get(c.chunk_id)}
        for c in chunks
    ]


def retrieve(
    conn: psycopg.Connection, tenant_id: str, question: str, mode: str = "dense", k: int = TOP_K
) -> Retrieval:
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"unknown retrieval mode: {mode}")
    query_vec = embed_one(question)
    if mode == "dense":
        chunks = db.search(conn, tenant_id, query_vec, k)
        ids = [c.chunk_id for c in chunks]
        return Retrieval(chunks, _details(chunks, _ranks(ids), {}), ids)

    dense = db.search(conn, tenant_id, query_vec, CANDIDATES)
    index = keyword_index(conn, tenant_id)
    keyword_ids = [index.chunks[i].chunk_id for i in index.bm25.top(tokenize(question), CANDIDATES)]
    dense_ids = [c.chunk_id for c in dense]
    fused = rrf_fuse([dense_ids, keyword_ids])[:CANDIDATES]
    by_id = {c.chunk_id: c for c in index.chunks}
    chunks = [replace(by_id[cid], score=score) for cid, score in fused[:k]]
    return Retrieval(chunks, _details(chunks, _ranks(dense_ids), _ranks(keyword_ids)), [cid for cid, _ in fused])


def warm_up(conn: psycopg.Connection, tenant_id: str, mode: str) -> None:
    """Load models and indexes up front so they aren't counted in the first question's latency."""
    embed_one("warm-up")
    if mode != "dense":
        keyword_index(conn, tenant_id)
        tokenize("warm-up")
