"""Line counts, severities, components, time range and input files."""

from __future__ import annotations

from collections import Counter

from logpacker.ingest.lines import LogRecord

SEVERITY_LABELS = {"E": "error", "W": "warning", "I": "info", "T": "trace", "V": "verbose", "D": "debug"}


class StatsCollector:
    name = "stats"

    def __init__(self) -> None:
        self.lines = 0
        self.records = 0
        self.severities: Counter = Counter()
        self.components: Counter = Counter()
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self.sources: dict[str, int] = {}

    def feed(self, record: LogRecord) -> None:
        self.lines += 1
        self.sources[record.source] = self.sources.get(record.source, 0) + 1
        if record.continuation:
            return
        self.records += 1
        self.severities[record.severity or "?"] += 1
        self.components[record.component] += 1
        if record.ts:
            if self.first_ts is None:
                self.first_ts = record.ts
            self.last_ts = record.ts

    def result(self) -> dict:
        return {
            "lines": self.lines,
            "records": self.records,
            "errors": self.severities.get("E", 0),
            "warnings": self.severities.get("W", 0),
            "severities": {SEVERITY_LABELS.get(k, k): v for k, v in self.severities.most_common()},
            "components": dict(self.components.most_common(30)),
            "first_ts": self.first_ts,
            "last_ts": self.last_ts,
            "sources": [{"name": n, "lines": c} for n, c in self.sources.items()],
        }
