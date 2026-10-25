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
    assert [c.chunk_id for c in chunks] == ["01_menu#コース料金", "01_menu#初検料"]
    assert all(c.doc_id == "01_menu" for c in chunks)


def test_preamble_is_merged_into_every_chunk():
    chunks = split_markdown("01_menu", DOC)
    assert all("2026年4月1日改定" in c.text for c in chunks)


def test_chunk_text_carries_title_preamble_and_section():
    course = split_markdown("01_menu", DOC)[0]
    assert course.text == "# 料金表\n2026年4月1日改定\n## コース料金\n60分 7,150円"


def test_no_preamble_gives_title_and_section_only():
    chunks = split_markdown("02_hours", "# 営業時間\n\n## 定休日\n毎週水曜日\n")
    assert chunks[0].text == "# 営業時間\n## 定休日\n毎週水曜日"


def test_doc_without_h2_is_single_chunk():
    chunks = split_markdown("06_faq", "# よくある質問\n\n**Q. 領収書は？**\nA. 発行します。\n")
    assert len(chunks) == 1
    assert chunks[0].chunk_id == "06_faq#よくある質問"
    assert chunks[0].text.startswith("# よくある質問\n**Q. 領収書は？**")


def test_empty_preamble_is_skipped_and_duplicates_suffixed():
    chunks = split_markdown("x", "# T\n## A\none\n## A\ntwo\n")
    assert [c.section for c in chunks] == ["A", "A-2"]


def test_real_news_doc_has_four_dated_sections():
    chunks = [c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs") if c.doc_id == "07_news"]
    assert len(chunks) == 4
    assert chunks[0].section.startswith("2026年10月15日")


def test_eval_gold_chunks_exist_and_match_sources():
    import json

    from src.config import EVAL_DIR

    chunk_ids = {c.chunk_id for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs")}
    for line in (EVAL_DIR / "questions.jsonl").read_text(encoding="utf-8").splitlines():
        q = json.loads(line)
        gold = q.get("gold_chunks", [])
        assert bool(gold) == bool(q["sources"]), q["id"]
        assert set(gold) <= chunk_ids, q["id"]
        assert {g.split("#")[0] for g in gold} <= set(q["sources"]), q["id"]


def test_real_menu_doc_carries_tax_note_in_every_chunk():
    chunks = [c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs") if c.doc_id == "01_menu_prices"]
    assert len(chunks) == 8
    assert all("税込" in c.text for c in chunks)
