"""Chunk a tenant's docs, embed them and upsert into pgvector. Safe to re-run.

Usage: python -m src.ingest [--tenant komorebi-seitai-demo]
"""

from __future__ import annotations

import argparse
import time

from src import db
from src.chunking import load_tenant_chunks
from src.config import DATA_DIR, DEFAULT_TENANT
from src.embeddings import embed


def ingest(tenant_id: str) -> None:
    docs_dir = DATA_DIR / tenant_id / "docs"
    if not docs_dir.is_dir():
        raise SystemExit(f"Docs folder not found: {docs_dir}")

    chunks = load_tenant_chunks(docs_dir)
    print(f"[{tenant_id}] {len(chunks)} chunks from {len({c.doc_id for c in chunks})} docs")

    started = time.perf_counter()
    embeddings = embed([c.text for c in chunks])
    print(f"Embedded in {time.perf_counter() - started:.1f}s")

    with db.connect() as conn:
        db.ensure_schema(conn)
        deleted = db.replace_tenant_chunks(conn, tenant_id, chunks, embeddings)
        total = conn.execute(
            "SELECT count(*) FROM chunks WHERE tenant_id = %s", (tenant_id,)
        ).fetchone()[0]
    print(f"Upserted {len(chunks)}, removed {deleted} stale; {total} chunks stored for tenant")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    ingest(parser.parse_args().tenant)


if __name__ == "__main__":
    main()
