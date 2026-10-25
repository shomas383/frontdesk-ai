from datetime import date

from src.chunking import load_tenant_chunks
from src.config import DATA_DIR, DEFAULT_TENANT
from src.db import RetrievedChunk
from src.doc_meta import DATE_FIELDS, DOC_TYPES, FIELDS, chunk_meta, load_doc_meta
from src.prompts import format_context

META = {
    "01": {"doc_type": "official", "effective": "2026-04-01"},
    "07": {"doc_type": "news", "sections": {"夏季": {"published": "2025-07-20", "valid_until": "2025-08-16"}}},
}


def _chunk(doc_id, section):
    return RetrievedChunk(f"{doc_id}#{section}", doc_id, section, "本文", 0.9)


def test_chunk_meta_merges_section_overrides_in_header_order():
    assert chunk_meta(META, "07", "夏季") == {"doc_type": "news", "published": "2025-07-20", "valid_until": "2025-08-16"}
    assert chunk_meta(META, "07", "other") == {"doc_type": "news"}
    assert chunk_meta(META, "missing", "x") == {}


def test_format_context_adds_metadata_attributes():
    ctx = format_context([_chunk("01", "コース料金")], META)
    assert '<chunk doc_id="01" section="コース料金" doc_type="official" effective="2026-04-01">' in ctx


def test_format_context_without_meta_is_unchanged():
    assert format_context([_chunk("01", "コース料金")]) == (
        '<context>\n<chunk doc_id="01" section="コース料金">\n本文\n</chunk>\n</context>'
    )


def test_format_context_escapes_attribute_values():
    assert 'section="a&quot;b"' in format_context([_chunk("01", 'a"b')])


def test_real_doc_meta_covers_every_doc_and_section():
    meta = load_doc_meta(DEFAULT_TENANT)
    chunks = load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs")
    sections = {(c.doc_id, c.section) for c in chunks}
    assert set(meta) == {c.doc_id for c in chunks}
    for doc_id, doc in meta.items():
        assert doc["doc_type"] in DOC_TYPES, doc_id
        for section, fields in doc.get("sections", {}).items():
            assert (doc_id, section) in sections, (doc_id, section)
            assert set(fields) <= set(FIELDS) - {"doc_type"}, (doc_id, section)
        for fields in [doc, *doc.get("sections", {}).values()]:
            for key in DATE_FIELDS:
                if key in fields:
                    date.fromisoformat(fields[key])
            assert set(fields) - {"sections"} <= set(FIELDS), doc_id


def test_tenant_without_meta_file_gets_empty_meta():
    assert load_doc_meta("no-such-tenant") == {}


def test_doc_meta_rule_only_in_prompt_when_enabled():
    from src.prompts import SYSTEM_PROMPT, prompt_hash, system_prompt

    assert "doc_type" not in SYSTEM_PROMPT and "{doc_meta_rule}" not in SYSTEM_PROMPT
    assert "doc_type" in system_prompt(doc_meta=True)
    assert prompt_hash() != prompt_hash(doc_meta=True)


def test_doc_meta_flag_is_off_by_default():
    from src.eval import build_parser

    assert build_parser().parse_args(["--name", "x"]).doc_meta is False
    assert build_parser().parse_args(["--name", "x", "--doc-meta"]).doc_meta is True
