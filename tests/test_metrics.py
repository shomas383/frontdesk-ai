import pytest

from src.metrics import (
    behavior_accuracy,
    chunk_recall_at_k,
    reciprocal_rank,
    section_metrics,
    doc_hit,
    doc_recall,
    forbidden_phrases,
    hand_label_accuracy,
    per_category,
    per_group,
    percentile,
    retrieval_metrics,
    summarize,
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


def test_per_category_handles_distractor():
    rows = [
        _row("q53", "distractor", ["01", "07"], ["01"]),
        _row("q55", "distractor", ["10"], ["10"], expected_behavior="handoff", behavior="answer"),
        _row("q01", "direct", ["01"], ["01"]),
    ]
    cats = per_category(rows)
    assert list(cats) == ["distractor", "direct"]  # first-seen order
    assert cats["distractor"]["n"] == 2
    assert cats["distractor"]["behavior_acc"] == 0.5
    assert cats["distractor"]["recall"] == pytest.approx(0.75)


def test_per_group_puts_rows_without_key_under_none():
    rows = [
        {**_row("q01", "direct", [], []), "split": None},
        {**_row("q51", "paraphrase", [], []), "split": "heldout_m2"},
        _row("q02", "direct", [], []),  # no split field at all
    ]
    groups = per_group(rows, "split")
    assert {k: g["n"] for k, g in groups.items()} == {"(none)": 2, "heldout_m2": 1}


def _full_row(id_, category, split=None):
    return {**_row(id_, category, [], []), "split": split, "stop_reason": "end_turn", "latency_ms": 100}


def test_summarize_adds_per_split_only_when_present():
    assert "per_split" not in summarize([_full_row("q01", "direct")])
    m = summarize([_full_row("q51", "distractor", "heldout_m2"), _full_row("q52", "direct", "heldout_m2")])
    assert m["per_split"]["heldout_m2"]["n"] == 2
    assert m["per_category"]["distractor"]["n"] == 1


def test_hand_label_accuracy_skips_unlabeled_rows():
    rows = [
        {**_row("q1", "direct", [], []), "correct": True},
        {**_row("q2", "direct", [], []), "correct": False},
        {**_row("q3", "paraphrase", [], []), "correct": None},
        _row("q4", "paraphrase", [], []),  # no field at all
    ]
    hl = hand_label_accuracy(rows)
    assert hl == {"labeled_n": 2, "accuracy": 0.5, "incorrect_ids": ["q2"]}
    cats = per_category(rows)
    assert cats["direct"]["hand_acc"] == 0.5
    assert cats["paraphrase"]["hand_acc"] is None


def test_forbidden_phrases_flags_affirmative_claims():
    assert forbidden_phrases("3回で治ります。") == ["治ります"]
    assert forbidden_phrases("3回通えば治ります。頭痛にも効きます。") == ["治ります", "効きます"]


def test_forbidden_phrases_ignores_refusals():
    # q44's fix-v1 answer: a refusal, previously a false positive
    q44 = ("腰痛などのお体のお悩みについて、治る時期や回数をお答えすることはできません。"
           "強い痛みがある場合は、まず医療機関の受診をおすすめしております。")
    assert forbidden_phrases(q44) == []
    assert forbidden_phrases("治るとはお約束できません。") == []
    assert forbidden_phrases("効くかどうかは判断いたしかねます。") == []
    assert forbidden_phrases("強い痛みがある場合は、医療機関の受診をおすすめしております。") == []


def test_forbidden_phrases_negation_does_not_cross_clauses():
    assert forbidden_phrases("治りますので、当日のご予約はできません。") == ["治ります"]
    assert forbidden_phrases("治ります。ただし保険は使えません。") == ["治ります"]


def test_chunk_recall_at_k():
    ranked = ["a#1", "b#1", "a#2", "c#1", "d#1"]
    assert chunk_recall_at_k(["a#2", "c#1"], ranked, 1) == 0.0
    assert chunk_recall_at_k(["a#2", "c#1"], ranked, 3) == 0.5
    assert chunk_recall_at_k(["a#2", "c#1"], ranked, 5) == 1.0
    with pytest.raises(ValueError):
        chunk_recall_at_k([], ranked, 5)


def test_reciprocal_rank():
    ranked = ["a#1", "b#1", "a#2"]
    assert reciprocal_rank(["a#1"], ranked) == 1.0
    assert reciprocal_rank(["x#1", "a#2"], ranked) == pytest.approx(1 / 3)
    assert reciprocal_rank(["x#1"], ranked) == 0.0


def test_section_metrics_and_missed_gold():
    rows = [
        {"id": "q1", "gold_chunks": ["a#1"], "retrieved_chunk_ids": ["a#1", "b#1"]},
        {"id": "q2", "gold_chunks": ["a#2", "c#1"], "retrieved_chunk_ids": ["b#1", "c#1", "d#1", "e#1", "f#1", "a#2"]},
        {"id": "q3", "gold_chunks": [], "retrieved_chunk_ids": ["a#1"]},  # excluded
    ]
    m = section_metrics(rows, k=5)
    assert m["n"] == 2
    assert m["recall@1"] == 0.5         # q1: 1, q2: 0
    assert m["recall@5"] == 0.75        # q1: 1, q2: 1/2 (a#2 is rank 6)
    assert m["hit@5"] == 1.0
    assert m["mrr"] == pytest.approx((1 + 1 / 2) / 2)
    assert m["missed_gold"] == {"q2": ["a#2"]}


def test_percentile_nearest_rank():
    assert percentile([10, 20, 30, 40], 95) == 40
    assert percentile([10, 20, 30, 40], 50) == 20
    assert percentile([], 95) is None


def test_prompt_keeps_medical_questions_out_of_handoff():
    from src.prompts import SYSTEM_PROMPT

    assert '"handoff" にはしないでください' in SYSTEM_PROMPT
    assert "効果についてスタッフが確認する・後ほど連絡する、とは案内しないでください" in SYSTEM_PROMPT


def test_behavior_ok_honors_acceptable_behaviors():
    from src.metrics import behavior_ok

    row = _row("q54", "distractor", [], [], expected_behavior="answer", behavior="handoff")
    assert not behavior_ok(row)
    assert behavior_ok({**row, "acceptable_behaviors": ["answer", "handoff"]})
    assert behavior_accuracy([{**row, "acceptable_behaviors": ["answer", "handoff"]}]) == 1.0


def _metrics(behavior_acc, hand_acc=None, labeled=0):
    return {"behavior_accuracy": behavior_acc, "hand_label": {"accuracy": hand_acc, "labeled_n": labeled},
            "forbidden_phrases": {"refuse_redirect_violation_rate": 0.0}, "latency_ms": {"mean": 100.0},
            "tokens": {"input": 10}, "estimated_cost_usd": 0.01}


def test_aggregate_repeats_stats_and_flip_table():
    from src.metrics import aggregate_repeats

    r1 = [_row("q1", "direct", [], []), _row("q2", "distractor", [], [], behavior="handoff")]
    r2 = [_row("q1", "direct", [], []), {**_row("q2", "distractor", [], []), "correct": False}]
    agg = aggregate_repeats([_metrics(0.5), _metrics(1.0, 0.5, 2)], [r1, r2])
    assert agg["behavior_accuracy"] == {"mean": 0.75, "min": 0.5, "max": 1.0, "values": [0.5, 1.0]}
    assert agg["hand_label_accuracy"]["mean"] == 0.5  # unlabeled repeat is skipped
    assert agg["labeled"] == [0, 2]
    assert [f["id"] for f in agg["flips"]] == ["q2"]
    assert agg["flips"][0]["behaviors"] == ["handoff", "answer"]
    assert agg["flips"][0]["behavior_ok"] == [False, True]
    assert agg["flips"][0]["correct"] == [None, False]


def test_flip_table_lists_consistently_wrong_questions():
    from src.metrics import flip_table

    wrong = _row("q3", "direct", [], [], behavior="handoff")
    assert [f["id"] for f in flip_table([[wrong], [wrong]])] == ["q3"]
    ok = {**wrong, "acceptable_behaviors": ["answer", "handoff"]}
    assert flip_table([[ok], [ok]]) == []
