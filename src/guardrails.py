"""Post-generation guardrails applied to the LLM's answer before it reaches the customer.

unstated_negative: the answer asserts that something is currently unavailable (現在は…ございません,
販売しておりません, 取り扱いがございません, ...) but nothing it cites can back a claim about the present:
every cited doc is a blog post, an expired campaign or news item, or nothing is cited at all. The
claim is removed deterministically and the answer becomes a handoff. Official docs (and news or
campaigns still within valid_until) count as support, so negatives like 「水曜日は定休日」 are untouched.
Needs data/<tenant>/doc_meta.json; tenants without it are never checked.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from src.db import RetrievedChunk
from src.doc_meta import DocMeta, chunk_meta

logger = logging.getLogger(__name__)

CURRENT = re.compile(r"現在|現時点|今は|もう")
NEGATIVE = re.compile(r"ございません|しておりません|扱っておりません|いただけません")
SENTENCE = re.compile(r"[^。！？!?]*[。！？!?]?")
HANDOFF = re.compile(r"スタッフ[^。]*(ご連絡|確認|ご案内)|確認のうえ")
HANDOFF_SENTENCE = "現在の状況については、確認のうえスタッフからご連絡いたします。"


@dataclass
class GuardrailResult:
    fired: bool
    answer: str
    behavior: str
    removed: list[str] = field(default_factory=list)

    def log_record(self, original_answer: str) -> dict[str, Any]:
        return {"rule": "unstated_negative", "fired": self.fired, "removed_sentences": self.removed,
                **({"original_answer": original_answer} if self.fired else {})}


def _sentences(text: str) -> list[str]:
    return [s for s in SENTENCE.findall(text) if s.strip()]


def asserts_current_unavailability(sentence: str) -> bool:
    current = CURRENT.search(sentence)
    return bool(current and NEGATIVE.search(sentence, current.end()))


def supports_current(doc_id: str, chunks: list[RetrievedChunk], meta: DocMeta, today: str) -> bool:
    """Can this cited doc back a claim about the present? Unknown docs are given the benefit of the doubt."""
    doc = meta.get(doc_id)
    if doc is None or doc.get("doc_type") == "official":
        return True
    if doc.get("doc_type") == "blog":
        return False
    # news / campaign: only sections that haven't expired count
    sections = [chunk_meta(meta, c.doc_id, c.section) for c in chunks if c.doc_id == doc_id]
    return any(s.get("valid_until", "9999-12-31") >= today for s in sections)


def check_unstated_negative(
    answer: str, behavior: str, sources: list[str], chunks: list[RetrievedChunk], meta: DocMeta, today: str
) -> GuardrailResult:
    unchanged = GuardrailResult(False, answer, behavior)
    if not meta:
        return unchanged
    flagged = [s for s in _sentences(answer) if asserts_current_unavailability(s)]
    if not flagged or any(supports_current(d, chunks, meta, today) for d in sources):
        return unchanged
    kept = "".join(s for s in _sentences(answer) if s not in flagged).strip()
    if not HANDOFF.search(kept):
        kept = f"{kept}{HANDOFF_SENTENCE}" if kept else HANDOFF_SENTENCE
    new_behavior = "handoff" if behavior == "answer" else behavior  # refuse_redirect stays as is
    logger.warning("guardrail unstated_negative fired; removed: %s", flagged)
    return GuardrailResult(True, kept, new_behavior, [s.strip() for s in flagged])
