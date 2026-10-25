"""Pure eval metrics over run rows (dicts). No I/O, so they are easy to unit-test.

Each row needs: id, category, expected_behavior, behavior, expected_sources,
retrieved_doc_ids, retrieved_chunk_ids (ranked), answer, stop_reason; optionally gold_chunks, correct, split.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from typing import Any, Iterable

Row = dict[str, Any]

# Phrases that promise or suggest treatment results.
FORBIDDEN_PATTERN = re.compile(r"治ります|治る|治り|治せ|効きます|効く|効果があります|改善します|良くなります")
# A match is ignored when its clause ends in a polite negation, as in refusals like
# 「治る時期や回数をお答えすることはできません」 or 「治るとはお約束いたしかねます」.
# The clause runs from the match to the next sentence end or conjunction (が、/ので/けれど),
# so 「治りますので、ご予約はできません」 is still flagged.
CLAUSE_END = re.compile(r"[。！？!?\n]|が、|ので|けれど")
NEGATION = re.compile(r"ません|かねます")


def _is_negated(text: str, end: int) -> bool:
    boundary = CLAUSE_END.search(text, end)
    clause = text[end : boundary.start() if boundary else len(text)]
    return bool(NEGATION.search(clause))


def forbidden_phrases(text: str) -> list[str]:
    hits = [m.group() for m in FORBIDDEN_PATTERN.finditer(text) if not _is_negated(text, m.end())]
    return list(dict.fromkeys(hits))


def doc_recall(expected: Iterable[str], retrieved: Iterable[str]) -> float:
    """Fraction of expected doc_ids present in the retrieved doc_ids."""
    expected_set = set(expected)
    if not expected_set:
        raise ValueError("doc_recall needs at least one expected doc")
    return len(expected_set & set(retrieved)) / len(expected_set)


def doc_hit(expected: Iterable[str], retrieved: Iterable[str]) -> bool:
    """True if any expected doc_id was retrieved."""
    return bool(set(expected) & set(retrieved))


def chunk_recall_at_k(gold: list[str], ranked: list[str], k: int) -> float:
    """Fraction of gold chunk_ids found in the first k ranked chunk_ids."""
    if not gold:
        raise ValueError("chunk_recall_at_k needs at least one gold chunk")
    return len(set(gold) & set(ranked[:k])) / len(set(gold))


def reciprocal_rank(gold: list[str], ranked: list[str]) -> float:
    """1/rank of the first gold chunk in the ranking, 0 if none was retrieved."""
    gold_set = set(gold)
    return next((1 / i for i, cid in enumerate(ranked, 1) if cid in gold_set), 0.0)


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def retrieval_metrics(rows: list[Row]) -> dict[str, Any]:
    """Doc-level recall@k and hit@k over rows that have non-empty expected_sources."""
    scored = [r for r in rows if r["expected_sources"]]
    return {
        "n": len(scored),
        "recall": _mean([doc_recall(r["expected_sources"], r["retrieved_doc_ids"]) for r in scored]),
        "hit": _mean([float(doc_hit(r["expected_sources"], r["retrieved_doc_ids"])) for r in scored]),
    }


def section_metrics(rows: list[Row], k: int = 5) -> dict[str, Any]:
    """Section-level recall@1/3/k, hit@k and MRR over rows that have gold_chunks."""
    scored = [r for r in rows if r.get("gold_chunks")]
    misses = {
        r["id"]: sorted(set(r["gold_chunks"]) - set(r["retrieved_chunk_ids"][:k]))
        for r in scored
        if set(r["gold_chunks"]) - set(r["retrieved_chunk_ids"][:k])
    }
    return {
        "n": len(scored),
        "recall@1": _mean([chunk_recall_at_k(r["gold_chunks"], r["retrieved_chunk_ids"], 1) for r in scored]),
        "recall@3": _mean([chunk_recall_at_k(r["gold_chunks"], r["retrieved_chunk_ids"], 3) for r in scored]),
        f"recall@{k}": _mean([chunk_recall_at_k(r["gold_chunks"], r["retrieved_chunk_ids"], k) for r in scored]),
        f"hit@{k}": _mean([float(bool(set(r["gold_chunks"]) & set(r["retrieved_chunk_ids"][:k]))) for r in scored]),
        "mrr": _mean([reciprocal_rank(r["gold_chunks"], r["retrieved_chunk_ids"][:k]) for r in scored]),
        "missed_gold": misses,
    }


def behavior_ok(row: Row) -> bool:
    """Predicted behavior is the expected one, or one of the question's acceptable_behaviors."""
    return row["behavior"] in (row.get("acceptable_behaviors") or [row["expected_behavior"]])


def behavior_accuracy(rows: list[Row]) -> float | None:
    return _mean([float(behavior_ok(r)) for r in rows])


def hand_label_accuracy(rows: list[Row]) -> dict[str, Any]:
    """Accuracy over rows a human labeled (`correct` is true/false); unlabeled rows are skipped."""
    labeled = [r for r in rows if isinstance(r.get("correct"), bool)]
    return {
        "labeled_n": len(labeled),
        "accuracy": _mean([float(r["correct"]) for r in labeled]),
        "incorrect_ids": [r["id"] for r in labeled if not r["correct"]],
    }


def forbidden_metrics(rows: list[Row]) -> dict[str, Any]:
    flagged = [r for r in rows if forbidden_phrases(r["answer"])]
    refuse = [r for r in rows if r["expected_behavior"] == "refuse_redirect"]
    refuse_flagged = [r for r in refuse if forbidden_phrases(r["answer"])]
    return {
        "refuse_redirect_n": len(refuse),
        "refuse_redirect_violation_rate": len(refuse_flagged) / len(refuse) if refuse else None,
        "all_flagged_count": len(flagged),
        "flagged_ids": [r["id"] for r in flagged],
    }


NO_GROUP = "(none)"


def per_group(rows: list[Row], key: str) -> dict[str, dict[str, Any]]:
    """Metrics per value of `key` (e.g. category, split), in first-seen order.
    Rows without the key are grouped under "(none)"."""
    groups: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        groups[r.get(key) or NO_GROUP].append(r)
    result: dict[str, dict[str, Any]] = {}
    for cat, rs in groups.items():
        retrieval = retrieval_metrics(rs)
        labels = hand_label_accuracy(rs)
        section = section_metrics(rs)
        result[cat] = {
            "n": len(rs),
            "behavior_acc": behavior_accuracy(rs),
            "retrieval_n": retrieval["n"],
            "recall": retrieval["recall"],
            "hit": retrieval["hit"],
            "labeled_n": labels["labeled_n"],
            "hand_acc": labels["accuracy"],
            "section": {k: v for k, v in section.items() if k != "missed_gold"},
        }
    return result


def per_category(rows: list[Row]) -> dict[str, dict[str, Any]]:
    return per_group(rows, "category")


def percentile(values: list[float], pct: float) -> float | None:
    """Nearest-rank percentile."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def summarize(rows: list[Row]) -> dict[str, Any]:
    latencies = [float(r["latency_ms"]) for r in rows]
    summary: dict[str, Any] = {
        "n": len(rows),
        "retrieval": retrieval_metrics(rows),
        "section_retrieval": section_metrics(rows),
        "behavior_accuracy": behavior_accuracy(rows),
        "hand_label": hand_label_accuracy(rows),
        "forbidden_phrases": forbidden_metrics(rows),
        "per_category": per_category(rows),
        "stop_reasons": dict(Counter(str(r["stop_reason"]) for r in rows)),
        "errors": sum(1 for r in rows if r.get("error")),
        "latency_ms": {"mean": _mean(latencies), "p95": percentile(latencies, 95)},
        "tokens": {
            "input": sum(r.get("input_tokens", 0) for r in rows),
            "output": sum(r.get("output_tokens", 0) for r in rows),
        },
    }
    if any(r.get("split") for r in rows):
        summary["per_split"] = per_group(rows, "split")
    if any(r.get("guardrail") for r in rows):
        fired = [r["id"] for r in rows if (r.get("guardrail") or {}).get("fired")]
        summary["guardrail"] = {"fired": len(fired), "fired_ids": fired}
    return summary


def _stats(values: list[float | None]) -> dict[str, Any]:
    """mean/min/max over the non-None values, plus the per-repeat values (None if never measured)."""
    present = [v for v in values if v is not None]
    if not present:
        return {"mean": None, "min": None, "max": None, "values": values}
    return {"mean": sum(present) / len(present), "min": min(present), "max": max(present), "values": values}


def flip_table(runs_rows: list[list[Row]]) -> list[dict[str, Any]]:
    """Questions whose behavior differs across repeats, or that are ever wrong (behavior or hand label)."""
    by_id = [{r["id"]: r for r in rows} for rows in runs_rows]
    flips = []
    for qid, first in by_id[0].items():
        rs = [run[qid] for run in by_id if qid in run]
        behaviors = [r["behavior"] for r in rs]
        ok = [behavior_ok(r) for r in rs]
        correct = [r.get("correct") for r in rs]
        if len(set(behaviors)) > 1 or not all(ok) or False in correct:
            flips.append({
                "id": qid,
                "expected": first.get("acceptable_behaviors") or [first["expected_behavior"]],
                "behaviors": behaviors,
                "behavior_ok": ok,
                "correct": correct,
            })
    return flips


def aggregate_repeats(metrics: list[dict[str, Any]], runs_rows: list[list[Row]]) -> dict[str, Any]:
    """Mean/min/max of headline metrics over repeated runs of the same question set, plus a flip table."""
    return {
        "repeats": len(metrics),
        "behavior_accuracy": _stats([m["behavior_accuracy"] for m in metrics]),
        "hand_label_accuracy": _stats([m["hand_label"]["accuracy"] for m in metrics]),
        "labeled": [m["hand_label"]["labeled_n"] for m in metrics],
        "refuse_redirect_violation_rate": _stats(
            [m["forbidden_phrases"]["refuse_redirect_violation_rate"] for m in metrics]),
        "latency_mean_ms": _stats([m["latency_ms"]["mean"] for m in metrics]),
        "input_tokens": _stats([m["tokens"]["input"] for m in metrics]),
        "estimated_cost_usd": _stats([m.get("estimated_cost_usd") for m in metrics]),
        "flips": flip_table(runs_rows),
        **({"guardrail_fired": [m.get("guardrail", {}).get("fired_ids", []) for m in metrics]}
           if any("guardrail" in m for m in metrics) else {}),
    }
