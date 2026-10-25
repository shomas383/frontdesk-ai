"""Run the eval set, save per-question results and print metrics.

Usage: python -m src.eval --name baseline [--questions eval/questions.jsonl] [--retrieval dense|hybrid]
                          [--limit N] [--tenant ...] [--today 2026-10-24]
Output: eval/runs/<YYYYMMDD-HHMMSS>_<name>.jsonl and .summary.json
"""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from src import db
from src.answer import answer_question
from src.config import DEFAULT_TENANT, EVAL_DIR, MAX_TOKENS, ROOT_DIR, TOP_K, get_settings
from src.llm import get_provider
from src.metrics import Row, forbidden_phrases, summarize
from src.prompts import prompt_hash
from src.retrieval import RETRIEVAL_MODES, retrieval_config, warm_up

# USD per million tokens (prompts <= 100K tokens).
# Source: https://platform.claude.com/docs/en/about-claude/pricing (checked 2026-10-24).
PRICING_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.00, 5.00),
}

# The dataset's notices are written relative to late October 2026; pin "today" for reproducibility.
DEFAULT_EVAL_TODAY = "2026-10-24"

DEFAULT_QUESTIONS = EVAL_DIR / "questions.jsonl"


def load_questions(path: Path, limit: int | None) -> list[dict[str, Any]]:
    with path.open(encoding="utf-8") as f:
        questions = [json.loads(line) for line in f if line.strip()]
    return questions[:limit] if limit else questions


def questions_file_label(path: Path) -> str:
    """Repo-relative POSIX path for the run config (absolute if outside the repo)."""
    try:
        return path.resolve().relative_to(ROOT_DIR.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def questions_path_from_config(config: dict[str, Any]) -> Path:
    """The question file a run used. Runs from before --questions existed used the default."""
    name = config.get("questions_file")
    return ROOT_DIR / name if name else DEFAULT_QUESTIONS


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", *args], cwd=ROOT_DIR, capture_output=True, text=True, check=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip()


def git_provenance() -> dict[str, Any]:
    """Commit + dirty flag for the code. Run outputs under eval/runs/ don't count as dirty."""
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain", "--", ".", ":(exclude)eval/runs") if commit else None
    return {"git_commit": commit, "git_dirty": bool(status) if commit else None}


def estimate_cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float | None:
    prices = next((p for name, p in PRICING_PER_MTOK.items() if name in model_id), None)
    if prices is None:
        return None
    return (input_tokens * prices[0] + output_tokens * prices[1]) / 1_000_000


def run_eval(
    name: str,
    tenant_id: str,
    today: str,
    limit: int | None,
    questions_path: Path = DEFAULT_QUESTIONS,
    retrieval: str = "dense",
) -> tuple[Path, list[Row]]:
    questions = load_questions(questions_path, limit)
    provenance = git_provenance()  # before writing any output
    provider = get_provider()
    p_hash = prompt_hash()
    runs_dir = EVAL_DIR / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    out_path = runs_dir / f"{datetime.now():%Y%m%d-%H%M%S}_{name}.jsonl"

    rows: list[Row] = []
    with db.connect() as conn, out_path.open("w", encoding="utf-8") as out:
        warm_up(conn, tenant_id, retrieval)  # so model/index loading isn't counted in q01's latency
        for i, q in enumerate(questions, 1):
            res = answer_question(conn, provider, tenant_id, q["question"], today, retrieval=retrieval)
            row: Row = {
                "id": q["id"],
                "category": q["category"],
                "split": q.get("split"),
                "question": q["question"],
                "expected_behavior": q["expected_behavior"],
                "expected_answer": q["expected_answer"],
                "expected_sources": q["sources"],
                "gold_chunks": q.get("gold_chunks", []),
                "trap": q.get("trap"),
                "retrieved_chunk_ids": res.retrieved_chunk_ids,
                "retrieved_doc_ids": res.retrieved_doc_ids,
                "retrieval_scores": [r["score"] for r in res.retrieved],
                "retrieval_ranks": [{"dense": r["dense_rank"], "keyword": r["keyword_rank"]} for r in res.retrieved],
                "candidate_chunk_ids": res.candidate_chunk_ids,
                "answer": res.answer,
                "behavior": res.behavior,
                "sources": res.sources,
                "invalid_sources": res.invalid_sources,
                "forbidden_phrases": forbidden_phrases(res.answer),
                "stop_reason": res.stop_reason,
                "latency_ms": res.latency_ms,
                "llm_latency_ms": res.llm_latency_ms,
                "input_tokens": res.input_tokens,
                "output_tokens": res.output_tokens,
                "error": res.error,
                "prompt_hash": p_hash,
                "correct": None,  # hand-label: true / false
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            rows.append(row)
            mark = "ok" if row["behavior"] == row["expected_behavior"] else "XX"
            print(f"[{i:>2}/{len(questions)}] {q['id']} {mark} {row['behavior']:<15} {res.latency_ms:>5}ms")

    config = {
        "tenant": tenant_id,
        "provider": get_settings().llm_provider,
        "model": provider.model_id,
        "embed_model": get_settings().embed_model,
        "top_k": TOP_K,
        **retrieval_config(retrieval),
        "max_tokens": MAX_TOKENS,
        "today": today,
        "questions_file": questions_file_label(questions_path),
        "system_prompt_sha256": p_hash,
        **provenance,
    }
    print_report(write_summary(out_path, config, rows))
    return out_path, rows


def summary_path(run_path: Path) -> Path:
    return run_path.with_suffix(".summary.json")


def load_rows(run_path: Path) -> list[Row]:
    with run_path.open(encoding="utf-8-sig") as f:
        return [json.loads(line) for line in f if line.strip()]


def write_rows(run_path: Path, rows: list[Row]) -> None:
    with run_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def refresh_gold_chunks(rows: list[Row], questions_path: Path) -> list[str]:
    """Set each row's gold_chunks from the run's question file (the source of truth for labels).
    Returns the ids whose gold_chunks changed."""
    gold = {q["id"]: q.get("gold_chunks", []) for q in load_questions(questions_path, None)}
    changed = []
    for row in rows:
        new = gold.get(row["id"], [])
        if row.get("gold_chunks") != new:
            row["gold_chunks"] = new
            changed.append(row["id"])
    return changed


def write_summary(run_path: Path, config: dict[str, Any], rows: list[Row]) -> dict[str, Any]:
    metrics = summarize(rows)
    tokens = metrics["tokens"]
    metrics["estimated_cost_usd"] = estimate_cost_usd(config["model"], tokens["input"], tokens["output"])
    summary = {"run": run_path.name, "config": config, "metrics": metrics}
    summary_path(run_path).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def resummarize(run_path: Path) -> dict[str, Any]:
    """Recompute metrics from a (possibly hand-labeled) run file, keeping the original config.
    gold_chunks are refreshed from the run's question file so label fixes apply to past runs."""
    config = json.loads(summary_path(run_path).read_text(encoding="utf-8"))["config"]
    rows = load_rows(run_path)
    questions_path = questions_path_from_config(config)
    changed = refresh_gold_chunks(rows, questions_path)
    if changed:
        write_rows(run_path, rows)
        print(f"Updated gold_chunks from {questions_path.name} for: {changed}")
    return write_summary(run_path, config, rows)


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value * 100:5.1f}%"


def print_report(summary: dict[str, Any]) -> None:
    m = summary["metrics"]
    print(f"\n=== {summary['run']} ({summary['config']['model']}) ===")
    config = summary["config"]
    print(f"questions: {config.get('questions_file', 'eval/questions.jsonl')}  retrieval: {config.get('retrieval', 'dense')}")
    print(f"doc recall@{TOP_K}: {_pct(m['retrieval']['recall'])}  hit@{TOP_K}: {_pct(m['retrieval']['hit'])}"
          f"  (n={m['retrieval']['n']} questions with sources)")
    s = m.get("section_retrieval")
    if s and s["n"]:
        print(f"section recall@1 {_pct(s['recall@1'])}  @3 {_pct(s['recall@3'])}  @{TOP_K} {_pct(s[f'recall@{TOP_K}'])}"
              f"  hit@{TOP_K} {_pct(s[f'hit@{TOP_K}'])}  MRR {s['mrr']:.3f}  (n={s['n']} with gold_chunks)")
        for qid, missing in s["missed_gold"].items():
            print(f"  gold chunk not in top-{TOP_K}: {qid} {missing}")
    print(f"behavior accuracy: {_pct(m['behavior_accuracy'])}  (n={m['n']})")
    hl = m["hand_label"]
    print(f"hand-label accuracy: {_pct(hl['accuracy'])}  (labeled {hl['labeled_n']}/{m['n']})"
          + (f"  incorrect: {hl['incorrect_ids']}" if hl["incorrect_ids"] else ""))
    fp = m["forbidden_phrases"]
    print(f"forbidden phrases on refuse_redirect: {_pct(fp['refuse_redirect_violation_rate'])}"
          f"  | flagged overall: {fp['all_flagged_count']} {fp['flagged_ids']}")
    print(f"stop_reasons: {m['stop_reasons']}  errors: {m['errors']}")
    lat, cost = m["latency_ms"], m["estimated_cost_usd"]
    cost_str = "n/a" if cost is None else f"${cost:.4f}"
    print(f"latency mean {lat['mean']:.0f}ms p95 {lat['p95']:.0f}ms | tokens in {m['tokens']['input']}"
          f" out {m['tokens']['output']} | est. cost {cost_str}")
    _print_group_table("category", m["per_category"])
    if "per_split" in m:
        _print_group_table("split", m["per_split"])


def _print_group_table(title: str, groups: dict[str, dict[str, Any]]) -> None:
    print(f"\n{title:<16}{'n':>4}{'behavior':>10}{'hand':>9}{'doc_rec':>9}{'doc_hit':>9}"
          f"{'sec_r@1':>9}{'sec_r@3':>9}{'sec_r@5':>9}{'sec_hit':>9}{'MRR':>7}")
    for group, c in groups.items():
        sec = c.get("section", {})
        mrr = sec.get("mrr")
        print(f"{group:<16}{c['n']:>4}{_pct(c['behavior_acc']):>10}{_pct(c['hand_acc']):>9}"
              f"{_pct(c['recall']):>9}{_pct(c['hit']):>9}{_pct(sec.get('recall@1')):>9}"
              f"{_pct(sec.get('recall@3')):>9}{_pct(sec.get(f'recall@{TOP_K}')):>9}"
              f"{_pct(sec.get(f'hit@{TOP_K}')):>9}{'  n/a' if mrr is None else f'{mrr:.3f}':>7}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", help="short label for this run, e.g. baseline")
    parser.add_argument("--questions", type=Path, default=DEFAULT_QUESTIONS,
                        help="question set to run (default: eval/questions.jsonl)")
    parser.add_argument("--retrieval", choices=RETRIEVAL_MODES, default="dense")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--today", default=DEFAULT_EVAL_TODAY)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resummarize", type=Path, metavar="RUN_JSONL",
                        help="recompute the summary of an existing (hand-labeled) run; no API calls")
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    if args.resummarize:
        print_report(resummarize(args.resummarize))
    elif args.name:
        if not args.questions.is_file():
            parser.error(f"questions file not found: {args.questions}")
        run_eval(args.name, args.tenant, args.today, args.limit, args.questions, args.retrieval)
    else:
        parser.error("pass --name to run the eval, or --resummarize RUN_JSONL")


if __name__ == "__main__":
    main()
