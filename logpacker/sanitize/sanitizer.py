"""Apply redaction rules line by line, learning sensitive literals as they appear."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from logpacker.sanitize.pseudonyms import PseudonymMap
from logpacker.sanitize.rules import CATEGORY_LABELS, OBJECT_RULE, RULES, Rule

_MAX_LEARNED = 300
_VALUE_GROUPS = ("v", "w")


@dataclass
class SanitizeOptions:
    enabled: bool = True
    mask_objects: bool = False
    custom_terms: list[str] = field(default_factory=list)


class Sanitizer:
    def __init__(self, options: SanitizeOptions | None = None):
        self.options = options or SanitizeOptions()
        self.map = PseudonymMap()
        self.rules: list[Rule] = list(RULES)
        if self.options.mask_objects:
            self.rules.append(OBJECT_RULE)
        self.lines_changed = 0
        self._learned: dict[str, str] = {}  # lower value -> category
        self._learned_re: re.Pattern | None = None
        self._custom_re = _literal_regex(t for t in self.options.custom_terms if t.strip())

    def apply(self, line: str) -> str:
        if not self.options.enabled or not line:
            return line
        out = line
        for rule in self.rules:
            if rule.needle is not None and rule.needle not in out:
                continue
            out = rule.pattern.sub(lambda m, r=rule: self._replace(r, m), out)
        if self._learned_re is not None:
            out = self._learned_re.sub(
                lambda m: self.map.token_for(self._learned[m.group(0).lower()], m.group(0)), out)
        if self._custom_re is not None:
            out = self._custom_re.sub(lambda m: self.map.token_for("term", m.group(0)), out)
        if out != line:
            self.lines_changed += 1
            for category in _categories_in(out):
                self.map.add_example(category, line, out)
        return out

    def _replace(self, rule: Rule, match: re.Match) -> str:
        groups = [g for g in _VALUE_GROUPS if g in rule.pattern.groupindex and match.group(g)]
        if not groups:
            value = match.group(0)
            return self._token(rule, value) if _accepted(rule, value) else value

        text, base = match.group(0), match.start()
        # Tokens are assigned left to right, then spliced right to left so offsets stay valid.
        tokens = [(name, self._token(rule, match.group(name))) for name in groups
                  if _accepted(rule, match.group(name))]
        for name, token in reversed(tokens):
            start, end = match.start(name) - base, match.end(name) - base
            text = text[:start] + token + text[end:]
        return text

    def _token(self, rule: Rule, value: str) -> str:
        if rule.fixed:
            self.map.hits[rule.category] += 1
            return rule.fixed
        if rule.learn:
            self._learn(rule.category, value)
        return self.map.token_for(rule.category, value)

    def _learn(self, category: str, value: str) -> None:
        key = value.lower()
        if key in self._learned or len(value) < 3 or len(self._learned) >= _MAX_LEARNED:
            return
        self._learned[key] = category
        self._learned_re = _literal_regex(self._learned)

    def summary(self) -> dict:
        distinct = self.map.distinct()
        categories = [
            {
                "category": cat,
                "label": CATEGORY_LABELS.get(cat, cat),
                "replacements": hits,
                "distinct": distinct.get(cat, 0),
                "examples": self.map.examples.get(cat, []),
            }
            for cat, hits in self.map.hits.most_common()
        ]
        return {
            "enabled": self.options.enabled,
            "mask_objects": self.options.mask_objects,
            "custom_terms": len(self.options.custom_terms),
            "lines_changed": self.lines_changed,
            "categories": categories,
        }


def _accepted(rule: Rule, value: str) -> bool:
    return rule.accept is None or rule.accept(value)


def _literal_regex(terms) -> re.Pattern | None:
    words = sorted({t for t in terms}, key=len, reverse=True)
    if not words:
        return None
    alternation = "|".join(re.escape(w) for w in words)
    return re.compile(rf"(?<![\w]){'(?:' + alternation + ')'}(?![\w])", re.IGNORECASE)


_TOKEN_RE = re.compile(r"\b(email|host|path|user|company|cloud|ip|obj|term|db)_\d{2,}\b|\[REDACTED\]")
_PREFIX_TO_CATEGORY = {"obj": "object", "[REDACTED]": "secret"}


def _categories_in(text: str) -> set[str]:
    found = set()
    for m in _TOKEN_RE.finditer(text):
        prefix = m.group(1) or m.group(0)
        found.add(_PREFIX_TO_CATEGORY.get(prefix, prefix))
    return found
