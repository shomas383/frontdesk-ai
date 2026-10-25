"""Compare two eval runs: metric deltas plus a per-question diff for hand-labeling.

Usage: python -m src.compare BASE_RUN.jsonl NEW_RUN.jsonl [--carry-labels]

Writes <NEW_RUN>.compare.md with the questions whose answer changed, side by side.
--carry-labels copies `correct`/`label_note` from BASE into NEW for answers that are byte-identical,
then recomputes NEW's summary, so only the changed answers need labeling.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from src.eval import (
    load_rows,
    questions_path_from_config,
    refresh_gold_chunks,
    resummarize,
    summary_path,
    write_rows,
)
from src.metrics import Row, summarize

LABEL_FIELDS = ("correct", "label_note")


# Value kinds: "pct" (0-1 shown as %), "ratio" (0-1 shown as 0.000), "num" (counts, ms).
def _fmt(value: float | None, kind: str = "pct") -> str:
    if value is None:
        return "n/a"
    return {"pct": f"{value * 100:.1f}%", "ratio": f"{value:.3f}", "num": f"{value:.0f}"}[kind]


def _delta(a: float | None, b: float | None, kind: str = "pct") -> str:
    if a is None or b is None:
        return ""
    d = b - a
    return {"pct": f"{d * 100:+.1f}pt", "ratio": f"{d:+.3f}", "num": f"{d:+.0f}"}[kind]


def metric_lines(base: dict[str, Any], new: dict[str, Any]) -> list[tuple[str, float | None, float | None, str]]:
    bs, ns = base["section_retrieval"], new["section_retrieval"]
    lines = [
        ("doc recall@k", base["retrieval"]["recall"], new["retrieval"]["recall"], "pct"),
        ("doc hit@k", base["retrieval"]["hit"], new["retrieval"]["hit"], "pct"),
        ("section recall@1", bs["recall@1"], ns["recall@1"], "pct"),
        ("section recall@3", bs["recall@3"], ns["recall@3"], "pct"),
        ("section recall@5", bs["recall@5"], ns["recall@5"], "pct"),
        ("section hit@5", bs["hit@5"], ns["hit@5"], "pct"),
        ("section MRR", bs["mrr"], ns["mrr"], "ratio"),
        ("behavior accuracy", base["behavior_accuracy"], new["behavior_accuracy"], "pct"),
        ("hand-label accuracy", base["hand_label"]["accuracy"], new["hand_label"]["accuracy"], "pct"),
        ("forbidden (refuse_redirect)", base["forbidden_phrases"]["refuse_redirect_violation_rate"],
         new["forbidden_phrases"]["refuse_redirect_violation_rate"], "pct"),
        ("latency mean ms", base["latency_ms"]["mean"], new["latency_ms"]["mean"], "num"),
        ("input tokens", base["tokens"]["input"], new["tokens"]["input"], "num"),
    ]
    bc, nc = base["per_category"], new["per_category"]
    for cat in [*bc, *(c for c in nc if c not in bc)]:  # a category may exist in only one run
        lines.append((f"  {cat} behavior", bc.get(cat, {}).get("behavior_acc"), nc.get(cat, {}).get("behavior_acc"), "pct"))
    return lines


def question_diffs(base_rows: list[Row], new_rows: list[Row]) -> list[dict[str, Any]]:
    base_by_id = {r["id"]: r for r in base_rows}
    diffs = []
    for new in new_rows:
        base = base_by_id.get(new["id"])
        if base is None:
            continue
        diffs.append({
            "id": new["id"],
            "base": base,
            "new": new,
            "answer_changed": base["answer"] != new["answer"],
            "behavior_changed": base["behavior"] != new["behavior"],
            "retrieval_changed": base["retrieved_chunk_ids"] != new["retrieved_chunk_ids"],
        })
    return diffs


def carry_labels(diffs: list[dict[str, Any]]) -> int:
    """Copy labels onto unchanged answers in place; returns how many rows were labeled."""
    carried = 0
    for d in diffs:
        if not d["answer_changed"] and isinstance(d["base"].get("correct"), bool):
            for field in LABEL_FIELDS:
                if field in d["base"]:
                    d["new"][field] = d["base"][field]
            carried += 1
    return carried


def _mark(row: Row) -> str:
    ok = row["behavior"] == row["expected_behavior"]
    return f"{row['behavior']} ({'ok' if ok else 'expected ' + row['expected_behavior']})"


def write_markdown(path: Path, base_name: str, new_name: str, diffs: list[dict[str, Any]]) -> None:
    changed = [d for d in diffs if d["answer_changed"]]
    out = [f"# {new_name} vs {base_name}", "",
           f"{len(changed)} of {len(diffs)} answers changed. Label `correct` in `{new_name}`.", ""]
    for d in changed:
        b, n = d["base"], d["new"]
        out += [
            f"## {d['id']} [{n['category']}] {n['question']}", "",
            f"- expected: {n['expected_answer']}" + (f"  (trap: {n['trap']})" if n.get("trap") else ""),
            f"- base label: {b.get('correct')}" + (f" — {b['label_note']}" if b.get("label_note") else ""),
            f"- behavior: {_mark(b)} → {_mark(n)}",
            f"- retrieval changed: {'yes' if d['retrieval_changed'] else 'no'}", "",
            f"**base:** {b['answer']}", "",
            f"**new:** {n['answer']}", "",
        ]
    path.write_text("\n".join(out), encoding="utf-8")


def compare(base_path: Path, new_path: Path, carry: bool) -> None:
    base_rows, new_rows = load_rows(base_path), load_rows(new_path)
    for path, rows in ((base_path, base_rows), (new_path, new_rows)):
        # in memory: score both runs against the current gold labels of their own question file
        config = json.loads(summary_path(path).read_text(encoding="utf-8"))["config"]
        refresh_gold_chunks(rows, questions_path_from_config(config))
    diffs = question_diffs(base_rows, new_rows)
    if carry:
        n = carry_labels(diffs)
        write_rows(new_path, new_rows)
        resummarize(new_path)
        print(f"Carried {n} labels from {base_path.name} for unchanged answers")

    base_m, new_m = summarize(base_rows), summarize(new_rows)
    print(f"\n{'metric':<30}{base_path.stem[:22]:>24}{new_path.stem[:22]:>24}{'delta':>10}")
    for name, a, b, kind in metric_lines(base_m, new_m):
        print(f"{name:<30}{_fmt(a, kind):>24}{_fmt(b, kind):>24}{_delta(a, b, kind):>10}")
    print(f"\nlabeled: base {base_m['hand_label']['labeled_n']}/{base_m['n']}, "
          f"new {new_m['hand_label']['labeled_n']}/{new_m['n']}")

    flips = [d for d in diffs if d["behavior_changed"]]
    print(f"\nbehavior changes ({len(flips)}):")
    for d in flips:
        print(f"  {d['id']}: {_mark(d['base'])} → {_mark(d['new'])}")
    changed = [d["id"] for d in diffs if d["answer_changed"]]
    print(f"answer text changed: {len(changed)}/{len(diffs)}")

    md_path = new_path.with_suffix(".compare.md")
    write_markdown(md_path, base_path.name, new_path.name, diffs)
    print(f"side-by-side for labeling: {md_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("base", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--carry-labels", action="store_true")
    args = parser.parse_args()
    for p in (args.base, args.new):
        if not summary_path(p).exists():
            parser.error(f"missing summary for {p}")
    compare(args.base, args.new, args.carry_labels)


if __name__ == "__main__":
    main()
