"""Count every line pattern (template). Drives condensing and the repetition summary."""

from __future__ import annotations

from logpacker.analysis.normalize import template_key
from logpacker.ingest.lines import LogRecord

MAX_TEMPLATES = 100_000
MAX_SAMPLES = 3
OVERFLOW_KEY = "*|*|<other>"


def record_key(record: LogRecord) -> str:
    if record.continuation:
        return template_key(record.component, "+", record.message)
    return template_key(record.component, record.severity, record.message)


class TemplatesCollector:
    name = "templates"

    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.first_line: dict[str, int] = {}
        self.sample_lines: dict[str, list[int]] = {}
        self._first_msg: dict[str, str] = {}
        self._all_same: dict[str, bool] = {}

    def feed(self, record: LogRecord) -> None:
        key = record_key(record)
        if key not in self.counts:
            if len(self.counts) >= MAX_TEMPLATES:
                key = OVERFLOW_KEY
                self.counts.setdefault(key, 0)
            else:
                self.counts[key] = 0
                self.first_line[key] = record.line_no
                self.sample_lines[key] = []
                self._first_msg[key] = record.message
                self._all_same[key] = True
        self.counts[key] += 1
        if self._all_same.get(key) and record.message != self._first_msg.get(key):
            self._all_same[key] = False
        samples = self.sample_lines.get(key)
        if samples is not None and len(samples) < MAX_SAMPLES:
            samples.append(record.line_no)

    def ids(self) -> dict[str, str]:
        """Stable ids by first appearance: P0001, P0002, ..."""
        ordered = sorted(self.first_line, key=self.first_line.get)
        return {key: f"P{i:04d}" for i, key in enumerate(ordered, 1)}

    def result(self, top: int = 40) -> dict:
        ids = self.ids()
        ranked = sorted(self.counts.items(), key=lambda kv: -kv[1])[:top]
        rows = []
        for key, count in ranked:
            component, severity, pattern = key.split("|", 2)
            all_same = self._all_same.get(key, False)
            display = self._first_msg[key] if all_same and key in self._first_msg else pattern
            rows.append({"id": ids.get(key, "P----"), "count": count, "component": component,
                         "severity": severity, "pattern": display,
                         "first_line": self.first_line.get(key),
                         "sample_lines": [] if all_same else self.sample_lines.get(key, [])})
        return {"distinct": len(self.counts), "top": rows}
