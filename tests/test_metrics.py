import pytest

from src.metrics import (
    behavior_accuracy,
    doc_hit,
    doc_recall,
    forbidden_phrases,
    per_category,
    percentile,
    retrieval_metrics,
)


def _row(id_, category, expected_sources, retrieved, expected_behavior="answer", behavior="answer"):
    return {
        "id": id_,
        "category": category,
        "expected_sources": expected_sources,
        "retrieved_doc_ids": retrieved,
        "expected_behavior": expected_behavior,
        "behavior": behavior,
        "answer": "",
    }


def test_doc_recall_partial_overlap():
    assert doc_recall(["01_menu_prices", "06_faq"], ["06_faq", "02_hours"]) == 0.5


def test_doc_recall_ignores_duplicate_retrieved_docs():
    assert doc_recall(["07_news"], ["07_news", "07_news", "02_hours"]) == 1.0


def test_doc_recall_requires_expected():
    with pytest.raises(ValueError):
        doc_recall([], ["01_menu_prices"])


def test_doc_hit():
    assert doc_hit(["01_menu_prices", "06_faq"], ["06_faq"])
    assert not doc_hit(["03_access"], ["06_faq"])


def test_retrieval_metrics_skips_questions_without_sources():
    rows = [
        _row("q1", "direct", ["01"], ["01", "02"]),
        _row("q2", "multi_doc", ["01", "07"], ["01"]),
        _row("q3", "out_of_scope", [], ["03"]),  # excluded
    ]
    m = retrieval_metrics(rows)
    assert m["n"] == 2
    assert m["recall"] == pytest.approx(0.75)
    assert m["hit"] == 1.0


def test_behavior_accuracy_and_per_category():
    rows = [
        _row("q1", "direct", ["01"], ["01"]),
        _row("q2", "out_of_scope", [], [], expected_behavior="handoff", behavior="answer"),
        _row("q3", "out_of_scope", [], [], expected_behavior="handoff", behavior="handoff"),
    ]
    assert behavior_accuracy(rows) == pytest.approx(2 / 3)
    cats = per_category(rows)
    assert cats["out_of_scope"]["behavior_acc"] == 0.5
    assert cats["out_of_scope"]["recall"] is None
    assert cats["direct"]["recall"] == 1.0


def test_forbidden_phrases():
    assert forbidden_phrases("3回通えば治ります。頭痛にも効きます。") == ["治ります", "効きます"]
    assert forbidden_phrases("強い痛みがある場合は、医療機関の受診をおすすめしております。") == []


def test_percentile_nearest_rank():
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([], 95) is None
