"""Postgres + pgvector storage for chunks."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import psycopg
from pgvector.psycopg import register_vector

from src.chunking import Chunk
from src.config import EMBED_DIM, get_settings

SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS chunks (
    tenant_id  text NOT NULL,
    chunk_id   text NOT NULL,
    doc_id     text NOT NULL,
    section    text NOT NULL,
    text       text NOT NULL,
    embedding  vector({EMBED_DIM}) NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, chunk_id)
);
"""

UPSERT_SQL = """
INSERT INTO chunks (tenant_id, chunk_id, doc_id, section, text, embedding)
VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (tenant_id, chunk_id) DO UPDATE SET
    doc_id = EXCLUDED.doc_id,
    section = EXCLUDED.section,
    text = EXCLUDED.text,
    embedding = EXCLUDED.embedding,
    updated_at = now();
"""

# Exact cosine scan (no ANN index): fine for tens of chunks and fully deterministic.
SEARCH_SQL = """
SELECT chunk_id, doc_id, section, text, 1 - (embedding <=> %(q)s) AS score
FROM chunks
WHERE tenant_id = %(tenant)s
ORDER BY embedding <=> %(q)s
LIMIT %(k)s;
"""


@dataclass(frozen=True)
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    section: str
    text: str
    score: float


def connect() -> psycopg.Connection:
    conn = psycopg.connect(get_settings().database_url)
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
    conn.commit()
    register_vector(conn)
    return conn


def ensure_schema(conn: psycopg.Connection) -> None:
    conn.execute(SCHEMA_SQL)
    conn.commit()


def replace_tenant_chunks(
    conn: psycopg.Connection, tenant_id: str, chunks: list[Chunk], embeddings: np.ndarray
) -> int:
    """Upsert all chunks, then delete this tenant's chunks that no longer exist. One transaction."""
    rows = [
        (tenant_id, c.chunk_id, c.doc_id, c.section, c.text, emb)
        for c, emb in zip(chunks, embeddings, strict=True)
    ]
    with conn.transaction(), conn.cursor() as cur:
        cur.executemany(UPSERT_SQL, rows)
        cur.execute(
            "DELETE FROM chunks WHERE tenant_id = %s AND NOT (chunk_id = ANY(%s));",
            (tenant_id, [c.chunk_id for c in chunks]),
        )
        return cur.rowcount


def search(
    conn: psycopg.Connection, tenant_id: str, query_vec: np.ndarray, k: int
) -> list[RetrievedChunk]:
    rows = conn.execute(SEARCH_SQL, {"q": query_vec, "tenant": tenant_id, "k": k}).fetchall()
    return [RetrievedChunk(*row[:4], score=float(row[4])) for row in rows]
