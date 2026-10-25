"""Redact phone numbers and email addresses from text before it is logged."""

from __future__ import annotations

import re
import unicodedata

PHONE_TOKEN = "[電話番号]"
EMAIL_TOKEN = "[メール]"

# ASCII-only local part and domain, so adjacent Japanese text (「taro@example.comまで」) is kept.
_EMAIL = re.compile(r"[A-Za-z0-9._%+\-]+[@＠][A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")
# Candidate digit runs with phone separators (half/full-width hyphens, long-vowel mark, spaces,
# parentheses). `\d` also matches full-width digits. Whether it's a phone is decided by its digits.
_SEP = r"[\s\-‐‑–—―－ー−()（）]"
_PHONE_CANDIDATE = re.compile(rf"(?<!\d)(?:[+＋]|[(（])?\d(?:{_SEP}{{0,2}}\d)+(?!\d)")


def _is_phone(candidate: str) -> bool:
    digits = [unicodedata.digit(ch) for ch in candidate if ch.isdigit()]
    if candidate[0] in "+＋":  # international: +81 then a 9–10 digit national number
        return digits[:2] == [8, 1] and 9 <= len(digits) - 2 <= 10
    return digits[0] == 0 and 10 <= len(digits) <= 11


def redact_pii(text: str) -> str:
    text = _EMAIL.sub(EMAIL_TOKEN, text)
    return _PHONE_CANDIDATE.sub(lambda m: PHONE_TOKEN if _is_phone(m.group()) else m.group(), text)
