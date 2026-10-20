"""Consistent pseudonyms: every distinct value in a category gets a stable token."""

from __future__ import annotations

from collections import Counter, defaultdict

from logpacker.sanitize.rules import TOKEN_PREFIX

MAX_EXAMPLES = 3


class PseudonymMap:
    def __init__(self) -> None:
        self._tokens: dict[tuple[str, str], str] = {}
        self._originals: dict[tuple[str, str], str] = {}
        self._next: Counter = Counter()
        self.hits: Counter = Counter()
        self.examples: dict[str, list[dict]] = defaultdict(list)

    def token_for(self, category: str, value: str) -> str:
        key = (category, value.lower())
        token = self._tokens.get(key)
        if token is None:
            self._next[category] += 1
            token = f"{TOKEN_PREFIX.get(category, category)}_{self._next[category]:02d}"
            self._tokens[key] = token
            self._originals[key] = value
        self.hits[category] += 1
        return token

    def add_example(self, category: str, before: str, after: str) -> None:
        bucket = self.examples[category]
        if len(bucket) < MAX_EXAMPLES:
            bucket.append({"before": before[:400], "after": after[:400]})

    def entries(self) -> list[dict]:
        """Mapping rows for local review only; never included in a package."""
        return [
            {"category": cat, "original": self._originals[(cat, low)], "token": token}
            for (cat, low), token in self._tokens.items()
        ]

    def distinct(self) -> Counter:
        return Counter(cat for cat, _ in self._tokens)
