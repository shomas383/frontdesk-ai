"""Per-document metadata (doc_type and dates) shown to the LLM in each chunk's context header.

Lives in data/<tenant>/doc_meta.json, keyed by doc_id, with optional per-section overrides:
  {"07_news": {"doc_type": "news", "sections": {"<section>": {"published": "2026-10-15"}}}}
It is never embedded, so retrieval is unaffected. Tenants without the file get plain headers.
"""

from __future__ import annotations

import hashlib
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.config import DATA_DIR

DOC_TYPES = ("official", "news", "blog", "campaign")
DATE_FIELDS = ("published", "effective", "valid_until")
FIELDS = ("doc_type", *DATE_FIELDS)  # also the attribute order in the context header

DocMeta = dict[str, dict[str, Any]]


def meta_path(tenant_id: str) -> Path:
    return DATA_DIR / tenant_id / "doc_meta.json"


@lru_cache(maxsize=None)
def load_doc_meta(tenant_id: str) -> DocMeta:
    path = meta_path(tenant_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}


def meta_sha256(tenant_id: str) -> str | None:
    """Short fingerprint of the tenant's metadata file, recorded with eval runs."""
    path = meta_path(tenant_id)
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12] if path.is_file() else None


def chunk_meta(meta: DocMeta, doc_id: str, section: str) -> dict[str, str]:
    """Doc-level fields overlaid with the section's own fields, in header order."""
    doc = meta.get(doc_id, {})
    merged = {**{k: v for k, v in doc.items() if k != "sections"}, **doc.get("sections", {}).get(section, {})}
    return {k: merged[k] for k in FIELDS if k in merged}
