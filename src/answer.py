"""Answer one customer question: retrieval (top-k) + LLM with structured JSON output.

Usage: python -m src.answer "60分の全身整体はいくらですか？" [--tenant ...] [--today 2026-10-24]
                            [--retrieval dense|hybrid|rerank] [--doc-meta] [--guardrail] [--small-docs]
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass, field
from datetime import date

import anthropic
import psycopg

from src import db
from src.config import DEFAULT_TENANT, MAX_TOKENS, TOP_K
from src.doc_meta import load_doc_meta
from src.guardrails import check_unstated_negative
from src.llm import LLMProvider, get_provider
from src.prompts import ANSWER_SCHEMA, build_user_message, system_prompt
from src.retrieval import RETRIEVAL_MODES, expand_small_docs, load_small_docs, retrieve

# Shown to the customer whenever the pipeline fails; staff takes over.
FALLBACK_ANSWER = "申し訳ございません。ただいま確認いたしますので、スタッフからご連絡いたします。"


@dataclass
class AnswerResult:
    answer: str
    behavior: str
    sources: list[str]
    retrieved: list[dict[str, object]]  # [{chunk_id, doc_id, score, dense_rank, keyword_rank, ...}]
    latency_ms: int
    llm_latency_ms: int
    input_tokens: int = 0
    output_tokens: int = 0
    stop_reason: str | None = None
    invalid_sources: list[str] = field(default_factory=list)
    candidate_chunk_ids: list[str] = field(default_factory=list)
    guardrail: dict[str, object] | None = None  # set when the guardrail ran
    expanded_docs: list[str] = field(default_factory=list)  # small docs shown in full
    context_chunk_ids: list[str] = field(default_factory=list)  # chunks the LLM saw (incl. expanded docs)
    error: str | None = None

    @property
    def retrieved_chunk_ids(self) -> list[str]:
        return [str(r["chunk_id"]) for r in self.retrieved]

    @property
    def retrieved_doc_ids(self) -> list[str]:
        return list(dict.fromkeys(str(r["doc_id"]) for r in self.retrieved))


def _split_sources(cited: list[str], allowed: list[str]) -> tuple[list[str], list[str]]:
    """Keep cited doc_ids that were actually retrieved; return (valid, invalid)."""
    valid = [s for s in dict.fromkeys(cited) if s in allowed]
    invalid = [s for s in dict.fromkeys(cited) if s not in allowed]
    return valid, invalid


def answer_question(
    conn: psycopg.Connection,
    provider: LLMProvider,
    tenant_id: str,
    question: str,
    today: str,
    k: int = TOP_K,
    retrieval: str = "dense",
    doc_meta: bool = False,
    guardrail: bool = False,
    small_docs: bool = False,
) -> AnswerResult:
    started = time.perf_counter()
    found = retrieve(conn, tenant_id, question, retrieval, k)
    if small_docs:
        chunks, expanded, covered = expand_small_docs(found.chunks, tenant_id, load_small_docs(tenant_id))
    else:
        chunks, expanded, covered = found.chunks, [], [c.chunk_id for c in found.chunks]

    llm_started = time.perf_counter()
    error: str | None = None
    try:
        result = provider.generate_json(
            system_prompt(doc_meta),
            build_user_message(question, chunks, today, load_doc_meta(tenant_id) if doc_meta else None),
            ANSWER_SCHEMA,
            MAX_TOKENS,
        )
    except anthropic.APIError as exc:  # after the SDK's own retries
        result, error = None, f"{type(exc).__name__}: {exc}"
    llm_ms = int((time.perf_counter() - llm_started) * 1000)
    total_ms = int((time.perf_counter() - started) * 1000)

    base = AnswerResult(
        answer=FALLBACK_ANSWER, behavior="handoff", sources=[], retrieved=found.details,
        latency_ms=total_ms, llm_latency_ms=llm_ms, candidate_chunk_ids=found.candidate_chunk_ids, error=error,
        expanded_docs=expanded, context_chunk_ids=covered,
    )
    if result is None:
        return base
    base.input_tokens, base.output_tokens = result.input_tokens, result.output_tokens
    base.stop_reason = result.stop_reason
    if result.data is None:
        base.error = f"no valid JSON (stop_reason={result.stop_reason})"
        return base

    allowed = list(dict.fromkeys(c.doc_id for c in chunks))
    base.sources, base.invalid_sources = _split_sources(list(result.data["sources"]), allowed)
    base.answer = str(result.data["answer"])
    base.behavior = str(result.data["behavior"])
    if guardrail:
        check = check_unstated_negative(
            base.answer, base.behavior, base.sources, chunks, load_doc_meta(tenant_id), today
        )
        base.guardrail = check.log_record(base.answer)
        base.answer, base.behavior = check.answer, check.behavior
    return base


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--tenant", default=DEFAULT_TENANT)
    parser.add_argument("--today", default=date.today().isoformat())
    parser.add_argument("--retrieval", choices=RETRIEVAL_MODES, default="dense")
    parser.add_argument("--doc-meta", action="store_true", help="show doc_type/dates from doc_meta.json to the LLM")
    parser.add_argument("--guardrail", action="store_true", help="rewrite unsupported 'currently unavailable' claims")
    parser.add_argument("--small-docs", action="store_true", help="show small docs (retrieval.json) in full when hit")
    args = parser.parse_args()

    with db.connect() as conn:
        result = answer_question(
            conn, get_provider(), args.tenant, args.question, args.today,
            retrieval=args.retrieval, doc_meta=args.doc_meta, guardrail=args.guardrail, small_docs=args.small_docs,
        )
    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
