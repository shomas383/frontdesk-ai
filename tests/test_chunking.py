from src.chunking import load_tenant_chunks, split_markdown
from src.config import DATA_DIR, DEFAULT_TENANT

DOC = """\
# 料金表

2026年4月1日改定

## コース料金

60分 7,150円

## 初検料

2,200円
"""


def test_splits_on_h2_with_chunk_ids():
    chunks = split_markdown("01_menu", DOC)
    assert [c.chunk_id for c in chunks] == ["01_menu#料金表", "01_menu#コース料金", "01_menu#初検料"]
    assert all(c.doc_id == "01_menu" for c in chunks)


def test_preamble_becomes_chunk_named_after_title():
    preamble = split_markdown("01_menu", DOC)[0]
    assert preamble.section == "料金表"
    assert "2026年4月1日改定" in preamble.text


def test_chunk_text_carries_title_and_section():
    course = split_markdown("01_menu", DOC)[1]
    assert course.text == "# 料金表\n## コース料金\n60分 7,150円"


def test_doc_without_h2_is_single_chunk():
    chunks = split_markdown("06_faq", "# よくある質問\n\n**Q. 領収書は？**\nA. 発行します。\n")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "06_faq#よくある質問"


def test_empty_preamble_is_skipped_and_duplicates_suffixed():
    chunks = split_markdown("x", "# T\n## A\none\n## A\ntwo\n")
    assert [c.section for c in chunks] == ["A", "A-2"]


def test_real_news_doc_has_four_dated_sections():
    chunks = [c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs") if c.doc_id == "07_news"]
    assert len(chunks) == 4
    assert chunks[0].section.startswith("2026年10月15日")
