"""Retrieval strategies: dense (pgvector), hybrid (dense + BM25 via reciprocal rank fusion),
rerank (hybrid candidate pool reordered by a cross-encoder).

Every mode returns the top-k chunks passed to the LLM plus diagnostics (ranks per retriever and
the fused candidate pool), so a miss can be traced to the pool or to the final ranking.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any

import psycopg

from src import db
from src.chunking import split_markdown
from src.config import DATA_DIR, TOP_K
from src.db import RetrievedChunk
from src.embeddings import embed_one
from src.lexical import BM25, TOKENIZER_NAME, tokenize
from src.rerank import MAX_LENGTH, RERANK_MODEL, Scorer, rerank, score_pairs

RETRIEVAL_MODES = ("dense", "hybrid", "rerank")
CANDIDATES = 20  # per retriever, and the size of the fused candidate pool
RRF_K = 60  # standard reciprocal-rank-fusion constant


@dataclass
class Retrieval:
    chunks: list[RetrievedChunk]  # top-k, in the order shown to the LLM; score is mode-specific
    details: list[dict[str, Any]]  # per chunk: chunk_id, doc_id, score, dense/keyword/fused ranks
    candidate_chunk_ids: list[str] = field(default_factory=list)


def retrieval_config(mode: str) -> dict[str, Any]:
    """What the run config records about retrieval."""
    config: dict[str, Any] = {"retrieval": mode}
    if mode != "dense":
        config.update(candidates=CANDIDATES, rrf_k=RRF_K, tokenizer=TOKENIZER_NAME)
    if mode == "rerank":
        config.update(reranker=RERANK_MODEL, rerank_max_length=MAX_LENGTH)
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


def _details(chunks: list[RetrievedChunk], *rankings: tuple[str, list[str]]) -> list[dict[str, Any]]:
    """Per-chunk diagnostics: score plus its rank in each named ranking (None if absent)."""
    ranks = {name: _ranks(ids) for name, ids in rankings}
    return [
        {"chunk_id": c.chunk_id, "doc_id": c.doc_id, "score": round(c.score, 4),
         **{f"{name}_rank": r.get(c.chunk_id) for name, r in ranks.items()}}
        for c in chunks
    ]


def retrieve(
    conn: psycopg.Connection,
    tenant_id: str,
    question: str,
    mode: str = "dense",
    k: int = TOP_K,
    scorer: Scorer = score_pairs,
) -> Retrieval:
    if mode not in RETRIEVAL_MODES:
        raise ValueError(f"unknown retrieval mode: {mode}")
    query_vec = embed_one(question)
    if mode == "dense":
        chunks = db.search(conn, tenant_id, query_vec, k)
        ids = [c.chunk_id for c in chunks]
        return Retrieval(chunks, _details(chunks, ("dense", ids), ("keyword", [])), ids)

    dense = db.search(conn, tenant_id, query_vec, CANDIDATES)
    index = keyword_index(conn, tenant_id)
    keyword_ids = [index.chunks[i].chunk_id for i in index.bm25.top(tokenize(question), CANDIDATES)]
    dense_ids = [c.chunk_id for c in dense]
    fused = rrf_fuse([dense_ids, keyword_ids])[:CANDIDATES]
    fused_ids = [cid for cid, _ in fused]
    by_id = {c.chunk_id: c for c in index.chunks}
    rankings = (("dense", dense_ids), ("keyword", keyword_ids), ("fused", fused_ids))

    if mode == "hybrid":
        chunks = [replace(by_id[cid], score=score) for cid, score in fused[:k]]
    else:  # rerank the whole fused pool with the cross-encoder
        reranked = rerank(question, [by_id[cid] for cid in fused_ids], scorer)
        chunks = [replace(c, score=score) for c, score in reranked[:k]]
    return Retrieval(chunks, _details(chunks, *rankings), fused_ids)


def warm_up(conn: psycopg.Connection, tenant_id: str, mode: str) -> None:
    """Load models and indexes up front so they aren't counted in the first question's latency."""
    embed_one("warm-up")
    if mode != "dense":
        keyword_index(conn, tenant_id)
        tokenize("warm-up")
    if mode == "rerank":
        score_pairs("warm-up", ["warm-up"])


FULL_DOC_SECTION = "全文"


def load_small_docs(tenant_id: str) -> list[str]:
    """doc_ids whose full text replaces their chunks in the LLM context (data/<tenant>/retrieval.json)."""
    path = DATA_DIR / tenant_id / "retrieval.json"
    return list(json.loads(path.read_text(encoding="utf-8")).get("small_docs", [])) if path.is_file() else []


def expand_small_docs(
    chunks: list[RetrievedChunk], tenant_id: str, small_docs: list[str]
) -> tuple[list[RetrievedChunk], list[str], list[str]]:
    """Parent-document expansion for short docs: if any chunk of a small doc was retrieved, its chunks are
    replaced by one full-document block placed where the doc's first chunk ranked. Retrieval itself
    is untouched. Returns (context chunks, expanded doc_ids, chunk_ids covered by the context)."""
    context: list[RetrievedChunk] = []
    expanded: list[str] = []
    covered: list[str] = []
    for c in chunks:
        if c.doc_id not in small_docs:
            context.append(c)
            covered.append(c.chunk_id)
        elif c.doc_id not in expanded:
            text = (DATA_DIR / tenant_id / "docs" / f"{c.doc_id}.md").read_text(encoding="utf-8").strip()
            context.append(RetrievedChunk(f"{c.doc_id}#{FULL_DOC_SECTION}", c.doc_id, FULL_DOC_SECTION, text, c.score))
            expanded.append(c.doc_id)
            covered.extend(ch.chunk_id for ch in split_markdown(c.doc_id, text))
    return context, expanded, list(dict.fromkeys(covered))
