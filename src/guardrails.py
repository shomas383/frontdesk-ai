"""Post-generation guardrails applied to the LLM's answer before it reaches the customer.

unstated_negative: the answer asserts that something is unavailable now (現在は…ございません,
販売しておりません, ギフト券のご用意はございません, ...) but nothing it cites backs that claim. A cited doc
backs it only if it can speak about the present (official, or a news/campaign section still within
valid_until) AND the claim's subject (e.g. ギフト券) actually appears in that doc's text. Otherwise the
claim is removed deterministically and the answer becomes a handoff. "Not in our documents" sentences
(資料に記載がございません) are handoffs already and are left alone.
Needs data/<tenant>/doc_meta.json; tenants without it are never checked.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

from src.db import RetrievedChunk
from src.doc_meta import DocMeta, chunk_meta

logger = logging.getLogger(__name__)

CURRENT = re.compile(r"現在|現時点|今は|もう")
NEGATIVE = re.compile(r"ございません|しておりません|扱っておりません|いただけません")
# Unavailability of an offering, claimed even without 現在 (ギフト券のご用意はございません).
NO_OFFERING = re.compile(r"(用意|取り?扱い|取扱い)(は|が|も)?(ございません|して?おりません|いたしておりません)"
                         r"|(扱って|用意して|販売して)おりません")
# "This isn't in our documents" is a handoff, not a claim about the clinic.
NOT_IN_DOCS = re.compile(r"資料|記載")
# Restating that a dated campaign ended (〜は8月31日に終了しており、現在は実施しておりません) is the dated
# fact itself, not a claim about the category; product claims (販売/取り扱い/ご用意/ご購入) stay checked.
ENDED_CAMPAIGN = re.compile(r"終了[^。]*実施")
SENTENCE = re.compile(r"[^。！？!?]*[。！？!?]?")
HANDOFF = re.compile(r"スタッフ[^。]*(ご連絡|確認|ご案内)|確認のうえ")
HANDOFF_SENTENCE = "現在の状況については、確認のうえスタッフからご連絡いたします。"

# Topic words that say nothing about *what* is unavailable.
GENERIC_SUBJECTS = {"現在", "現時点", "今", "当院", "こちら", "そちら", "取り扱い", "取扱い", "用意", "販売",
                    "実施", "案内", "ご案内", "提供", "お取り扱い", "ご用意"}
NOUNISH = {"名詞", "接頭辞", "接尾辞"}
# Formal nouns end a compound (臨時休業のため11月12日は → 11月12日).
FORMAL_NOUNS = {"ため", "こと", "もの", "ところ", "よう", "際", "場合"}


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
    if NOT_IN_DOCS.search(sentence) or ENDED_CAMPAIGN.search(sentence):
        return False
    current = CURRENT.search(sentence)
    return bool(current and NEGATIVE.search(sentence, current.end())) or bool(NO_OFFERING.search(sentence))


def _normalize(text: str) -> str:
    return re.sub(r"\s", "", unicodedata.normalize("NFKC", text))


def _compound_before(morphemes: list[Any], end: int) -> str:
    """Surface of the noun compound ending right before index `end` (inside brackets if `end-1` is ）)."""
    i = end - 1
    if i >= 0 and morphemes[i].surface() in ("）", ")"):
        i -= 1
    parts: list[str] = []
    while i >= 0 and morphemes[i].part_of_speech()[0] in NOUNISH and morphemes[i].surface() not in FORMAL_NOUNS:
        parts.insert(0, morphemes[i].surface())
        i -= 1
    while parts and parts[0] in ("ご", "お"):  # honorific prefix
        parts.pop(0)
    return "".join(parts)


def claim_subject(sentence: str) -> str | None:
    """What the negative claim is about: the first non-generic noun compound marked as topic (は/が/も),
    stepping back over 「〜の」 when the topic itself is generic (紹介割引の案内は → 紹介割引)."""
    from src.lexical import _tokenizer

    ms = list(_tokenizer().tokenize(sentence))
    for idx, m in enumerate(ms):
        if m.part_of_speech()[0] != "助詞" or m.surface() not in ("は", "が", "も"):
            continue
        subject = _compound_before(ms, idx)
        start = idx - 1
        while subject in GENERIC_SUBJECTS and start > 0:
            # step back to the noun before a preceding の (ギフト券の取り扱いは → ギフト券)
            while start >= 0 and ms[start].part_of_speech()[0] in NOUNISH:
                start -= 1
            if start >= 0 and ms[start].surface() == "の":
                subject = _compound_before(ms, start)
                start -= 1
            else:
                break
        if subject and subject not in GENERIC_SUBJECTS:
            return subject
    return None


def supports_claim(
    doc_id: str, subject: str | None, chunks: list[RetrievedChunk], meta: DocMeta, today: str,
    doc_texts: dict[str, str],
) -> bool:
    """Can this cited doc back a negative claim about `subject` now? It must speak about the present
    (official, or a news/campaign section not past valid_until) and mention the subject. Docs missing
    from doc_meta are given the benefit of the doubt."""
    doc = meta.get(doc_id)
    if doc is None:
        return True
    doc_type = doc.get("doc_type")
    if doc_type == "blog":
        return False
    if doc_type != "official":
        sections = [chunk_meta(meta, c.doc_id, c.section) for c in chunks if c.doc_id == doc_id]
        if not any(s.get("valid_until", "9999-12-31") >= today for s in sections):
            return False
    text = doc_texts.get(doc_id)
    return bool(subject and text and _normalize(subject) in _normalize(text))


def check_unstated_negative(
    answer: str, behavior: str, sources: list[str], chunks: list[RetrievedChunk], meta: DocMeta, today: str,
    doc_texts: dict[str, str] | None = None,
) -> GuardrailResult:
    """doc_texts: full text per cited doc_id, used for the subject check (defaults to the chunk texts)."""
    unchanged = GuardrailResult(False, answer, behavior)
    if not meta:
        return unchanged
    if doc_texts is None:
        doc_texts = {}
        for c in chunks:
            doc_texts[c.doc_id] = doc_texts.get(c.doc_id, "") + "\n" + c.text
    flagged = []
    for s in _sentences(answer):
        if asserts_current_unavailability(s):
            subject = claim_subject(s)
            if not any(supports_claim(d, subject, chunks, meta, today, doc_texts) for d in sources):
                flagged.append(s)
    if not flagged:
        return unchanged
    kept = "".join(s for s in _sentences(answer) if s not in flagged).strip()
    if not HANDOFF.search(kept):
        kept = f"{kept}{HANDOFF_SENTENCE}" if kept else HANDOFF_SENTENCE
    new_behavior = "handoff" if behavior == "answer" else behavior  # refuse_redirect stays as is
    logger.warning("guardrail unstated_negative fired; removed: %s", flagged)
    return GuardrailResult(True, kept, new_behavior, [s.strip() for s in flagged])
