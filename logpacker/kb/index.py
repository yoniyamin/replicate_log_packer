"""Keyword search over the bundled Qlik KB articles (BM25, title-boosted, no dependencies)."""

from __future__ import annotations

import json
import math
import re
import threading
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

KB_FILE = Path(__file__).resolve().parent.parent / "data" / "kb_articles.json"
TITLE_BOOST = 3
K1, B = 1.2, 0.75

_WORD_RE = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
STOPWORDS = frozenset("""
a an and are as at be been but by can could did do does for from had has have how if in into is it its
may might no not of on or our out should so such than that the their then there these this those to
was were what when where which while who why will with would you your after before during via per all
any each other more most some only also using use used new get set run make version versions article
qlik replicate task tasks error errors warning warnings failed failure fail message messages occurred
issue issues problem cause resolution environment information internal see following
""".split())


def tokenize(text: str) -> list[str]:
    """Lower-case words without stopwords. Codes such as ``ora-01555`` are kept whole and split."""
    out: list[str] = []
    for word in _WORD_RE.findall(text.lower()):
        parts = re.split(r"[-_.]", word)
        if len(parts) > 1 and any(c.isdigit() for c in word):
            out.append(word)
        for part in parts:
            if len(part) < 2 or part in STOPWORDS or (part.isdigit() and len(part) < 3):
                continue
            out.append(part)
    return out


@dataclass(frozen=True)
class Hit:
    index: int
    score: float      # raw BM25
    coverage: float   # share of the query's IDF weight that this article matched, 0..1
    phrase: float     # share of the query's adjacent word pairs that also appear side by side, 0..1
    matched: tuple[str, ...]


class KBIndex:
    def __init__(self, articles: list[dict], built_at: str | None = None):
        self.articles = articles
        self.built_at = built_at
        self._tf: list[Counter] = []
        self._pairs: list[set[tuple[str, str]]] = []
        self._len: list[int] = []
        df: Counter = Counter()
        for a in articles:
            title, text = tokenize(a.get("title", "")), tokenize(a.get("text", ""))
            tf = Counter(text)
            for term in title:
                tf[term] += TITLE_BOOST
            self._tf.append(tf)
            self._pairs.append(_pairs(title) | _pairs(text))
            self._len.append(sum(tf.values()))
            df.update(tf.keys())
        n = len(articles) or 1
        self._avg_len = (sum(self._len) / n) or 1
        self._idf = {t: math.log(1 + (n - d + 0.5) / (d + 0.5)) for t, d in df.items()}

    def __len__(self) -> int:
        return len(self.articles)

    def idf(self, term: str) -> float:
        return self._idf.get(term, 0.0)

    def search(self, terms: list[str], limit: int = 10) -> list[Hit]:
        query = [t for t in dict.fromkeys(terms) if t in self._idf]
        total_idf = sum(self._idf[t] for t in query)
        if not query or total_idf == 0:
            return []
        known = [t for t in terms if t in self._idf]
        wanted_pairs = _pairs(known)
        hits = []
        for i, tf in enumerate(self._tf):
            matched = [t for t in query if t in tf]
            if not matched:
                continue
            norm = K1 * (1 - B + B * self._len[i] / self._avg_len)
            score = sum(self._idf[t] * tf[t] * (K1 + 1) / (tf[t] + norm) for t in matched)
            coverage = sum(self._idf[t] for t in matched) / total_idf
            phrase = len(wanted_pairs & self._pairs[i]) / len(wanted_pairs) if wanted_pairs else 1.0
            hits.append(Hit(i, score, coverage, phrase, tuple(matched)))
        hits.sort(key=lambda h: (-h.coverage * (1 + h.phrase), -h.score))
        return hits[:limit]


def _pairs(tokens: list[str]) -> set[tuple[str, str]]:
    return set(zip(tokens, tokens[1:]))


_lock = threading.Lock()
_cached: KBIndex | None = None


def get_index(path: Path = KB_FILE) -> KBIndex | None:
    """The shared index, built on first use; None when no KB file is bundled."""
    global _cached
    with _lock:
        if _cached is None and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            _cached = KBIndex(data.get("articles", []), data.get("built_at"))
        return _cached
