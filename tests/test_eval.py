import json
from pathlib import Path

from src.config import EVAL_DIR, ROOT_DIR
from src.eval import (
    DEFAULT_QUESTIONS,
    build_parser,
    load_questions,
    questions_file_label,
    questions_path_from_config,
    refresh_question_labels,
)


def _write_questions(path: Path, questions: list[dict]) -> Path:
    path.write_text("".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions), encoding="utf-8")
    return path


def test_questions_flag_defaults_to_original_set():
    assert build_parser().parse_args(["--name", "x"]).questions == DEFAULT_QUESTIONS
    args = build_parser().parse_args(["--name", "x", "--questions", "eval/questions_m2_heldout.jsonl"])
    assert args.questions == Path("eval/questions_m2_heldout.jsonl")


def test_load_questions_skips_blank_lines_and_limits(tmp_path):
    path = _write_questions(tmp_path / "q.jsonl", [{"id": "q51"}, {"id": "q52"}])
    path.write_text(path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert [q["id"] for q in load_questions(path, None)] == ["q51", "q52"]
    assert [q["id"] for q in load_questions(path, 1)] == ["q51"]


def test_refresh_gold_chunks_uses_given_question_file(tmp_path):
    path = _write_questions(tmp_path / "heldout.jsonl", [
        {"id": "q51", "gold_chunks": ["02_hours#営業時間"]},
        {"id": "q64", "gold_chunks": []},
    ])
    rows = [{"id": "q51", "gold_chunks": ["stale#x"]}, {"id": "q64", "gold_chunks": []}]
    assert refresh_question_labels(rows, path) == ["q51"]
    assert rows[0]["gold_chunks"] == ["02_hours#営業時間"]


def test_questions_file_round_trips_through_config():
    heldout = EVAL_DIR / "questions_m2_heldout.jsonl"
    label = questions_file_label(heldout)
    assert label == "eval/questions_m2_heldout.jsonl"
    assert questions_path_from_config({"questions_file": label}) == ROOT_DIR / label


def test_old_run_configs_fall_back_to_original_set():
    assert questions_path_from_config({"model": "claude-haiku-5-5"}) == DEFAULT_QUESTIONS


def test_refresh_question_labels_sets_acceptable_behaviors(tmp_path):
    path = _write_questions(tmp_path / "q.jsonl", [{"id": "q54", "acceptable_behaviors": ["answer", "handoff"]}])
    rows = [{"id": "q54", "gold_chunks": []}]
    assert refresh_question_labels(rows, path) == ["q54"]
    assert rows[0]["acceptable_behaviors"] == ["answer", "handoff"]


def test_repeats_flag():
    assert build_parser().parse_args(["--name", "x"]).repeats == 1
    assert build_parser().parse_args(["--name", "x", "--repeats", "3"]).repeats == 3


def test_reaggregate_picks_up_hand_labels(tmp_path):
    from src.eval import reaggregate, summary_path, write_aggregate, write_rows, write_summary

    qpath = _write_questions(tmp_path / "q.jsonl", [{"id": "q1"}, {"id": "q2"}])
    config = {"model": "claude-haiku-5-5", "questions_file": str(qpath)}
    runs = []
    for i in (1, 2):
        rows = [{"id": q, "category": "direct", "expected_behavior": "answer", "behavior": "answer",
                 "expected_sources": [], "retrieved_doc_ids": [], "retrieved_chunk_ids": [], "answer": "",
                 "stop_reason": "end_turn", "latency_ms": 100, "correct": None} for q in ("q1", "q2")]
        path = tmp_path / f"run-r{i}.jsonl"
        write_rows(path, rows)
        write_summary(path, config, rows)
        runs.append(path)
    agg_path = tmp_path / "run.repeats.json"
    assert write_aggregate(agg_path, "run", runs)["aggregate"]["labeled"] == [0, 0]
    labeled = [{**json.loads(line), "correct": True} for line in runs[0].read_text(encoding="utf-8").splitlines()]
    write_rows(runs[0], labeled)
    data = reaggregate(agg_path)
    assert data["aggregate"]["labeled"] == [2, 0]
    assert data["aggregate"]["hand_label_accuracy"]["mean"] == 1.0
    assert summary_path(runs[0]).exists()
