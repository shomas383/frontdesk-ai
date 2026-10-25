"""Japanese keyword search: SudachiPy tokenization + Okapi BM25. Pure Python, in memory."""

from __future__ import annotations

import math
from collections import Counter
from functools import lru_cache
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sudachipy import Tokenizer

TOKENIZER_NAME = "sudachi-core-A-normalized"

# Content-word parts of speech; particles, auxiliaries, prefixes and symbols are dropped.
CONTENT_POS = {"名詞", "動詞", "形容詞", "形状詞", "副詞"}
# Light verbs that carry no search meaning (normalized forms): する, ある, いる, なる.
STOPWORDS = {"為る", "有る", "居る", "成る"}


@lru_cache(maxsize=1)
def _tokenizer() -> Tokenizer:
    from sudachipy import Dictionary, SplitMode

    return Dictionary(dict="core").tokenizer(mode=SplitMode.A)


def tokenize(text: str) -> list[str]:
    """Sudachi mode A, normalized forms (お着替え→着替え, 子ども→子供), content words only, lowercased."""
    tokens = []
    for m in _tokenizer().tokenize(text):
        form = m.normalized_form().lower()
        if m.part_of_speech()[0] in CONTENT_POS and form not in STOPWORDS and form.strip():
            tokens.append(form)
    return tokens


class BM25:
    """Okapi BM25 over pre-tokenized documents."""

    def __init__(self, docs: list[list[str]], k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.tfs = [Counter(d) for d in docs]
        self.lengths = [len(d) for d in docs]
        self.avg_len = sum(self.lengths) / len(docs) if docs else 0.0
        df = Counter(term for tf in self.tfs for term in tf)
        n = len(docs)
        self.idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def scores(self, query: list[str]) -> list[float]:
        terms = [t for t in dict.fromkeys(query) if t in self.idf]
        out = []
        for tf, length in zip(self.tfs, self.lengths):
            norm = self.k1 * (1 - self.b + self.b * length / self.avg_len) if self.avg_len else self.k1
            out.append(sum(self.idf[t] * tf[t] * (self.k1 + 1) / (tf[t] + norm) for t in terms if t in tf))
        return out

    def top(self, query: list[str], n: int) -> list[int]:
        """Indices of the top-n documents with a positive score, best first (ties by index)."""
        scored = [(s, i) for i, s in enumerate(self.scores(query)) if s > 0]
        return [i for _, i in sorted(scored, key=lambda x: (-x[0], x[1]))[:n]]
