import pytest

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


QUESTION_FILES = ["questions.jsonl", "questions_m2_heldout.jsonl"]


def _questions(name):
    import json

    from src.config import EVAL_DIR

    return [json.loads(line) for line in (EVAL_DIR / name).read_text(encoding="utf-8").splitlines() if line.strip()]


@pytest.mark.parametrize("name", QUESTION_FILES)
def test_eval_gold_chunks_exist_and_are_within_sources(name):
    chunk_ids = {c.chunk_id for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs")}
    for q in _questions(name):
        gold = q.get("gold_chunks", [])
        # sources without gold is allowed when the docs don't answer the question (q41)
        assert set(gold) <= chunk_ids, q["id"]
        assert {g.split("#")[0] for g in gold} <= set(q["sources"]), q["id"]


def test_eval_question_ids_are_unique_across_files():
    ids = [q["id"] for name in QUESTION_FILES for q in _questions(name)]
    assert len(ids) == len(set(ids))


def test_heldout_questions_carry_split():
    assert {q.get("split") for q in _questions("questions_m2_heldout.jsonl")} == {"heldout_m2"}


def test_real_faq_doc_has_one_chunk_per_question():
    chunks = [c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs") if c.doc_id == "06_faq"]
    assert len(chunks) == 12
    assert "06_faq#支払い方法" in {c.chunk_id for c in chunks}


def test_real_menu_doc_carries_tax_note_in_every_chunk():
    chunks = [c for c in load_tenant_chunks(DATA_DIR / DEFAULT_TENANT / "docs") if c.doc_id == "01_menu_prices"]
    assert len(chunks) == 8
    assert all("税込" in c.text for c in chunks)
