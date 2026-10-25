from src.config import DEFAULT_TENANT
from src.db import RetrievedChunk
from src.metrics import context_metrics
from src.retrieval import FULL_DOC_SECTION, expand_small_docs, load_small_docs


def _c(chunk_id, score=0.5):
    doc_id, section = chunk_id.split("#", 1)
    return RetrievedChunk(chunk_id, doc_id, section, "本文", score)


def test_small_docs_config():
    assert load_small_docs(DEFAULT_TENANT) == ["01_menu_prices"]
    assert load_small_docs("no-such-tenant") == []


def test_expansion_replaces_small_doc_chunks_with_one_full_block_in_place():
    chunks = [_c("06_faq#支払い方法", 0.9), _c("01_menu_prices#お支払い方法", 0.8),
              _c("02_hours#営業時間", 0.7), _c("01_menu_prices#コース料金", 0.6)]
    context, expanded, covered = expand_small_docs(chunks, DEFAULT_TENANT, ["01_menu_prices"])
    assert [c.chunk_id for c in context] == ["06_faq#支払い方法", f"01_menu_prices#{FULL_DOC_SECTION}", "02_hours#営業時間"]
    full = context[1]
    assert full.score == 0.8 and full.section == FULL_DOC_SECTION
    assert "## 初検料" in full.text and "税込" in full.text  # whole doc, preamble included
    assert expanded == ["01_menu_prices"]
    assert "01_menu_prices#初検料" in covered and len(covered) == len(set(covered))
    assert covered[0] == "06_faq#支払い方法"


def test_no_expansion_without_a_small_doc_hit():
    chunks = [_c("02_hours#営業時間"), _c("06_faq#健康保険")]
    context, expanded, covered = expand_small_docs(chunks, DEFAULT_TENANT, ["01_menu_prices"])
    assert context == chunks and expanded == []
    assert covered == ["02_hours#営業時間", "06_faq#健康保険"]


def test_context_recall_counts_expanded_sections():
    row = {"id": "q35", "gold_chunks": ["01_menu_prices#初検料", "01_menu_prices#コース料金"],
           "retrieved_chunk_ids": ["01_menu_prices#コース料金", "07_news#x"]}
    assert context_metrics([row])["recall"] == 0.5  # no expansion: equals section recall
    expanded = {**row, "context_chunk_ids": ["01_menu_prices#コース料金", "01_menu_prices#初検料", "07_news#x"],
                "expanded_docs": ["01_menu_prices"]}
    m = context_metrics([expanded])
    assert m["recall"] == 1.0 and m["expanded_n"] == 1 and m["missed_gold"] == {}
