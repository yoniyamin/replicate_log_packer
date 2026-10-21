"""CDC pipeline health (sorter / target connection) and source-side health."""

from __future__ import annotations

import re
from collections import Counter

from logpacker.ingest.lines import LogRecord

_SORTER_MEMORY = re.compile(r"memory|overflow|buffer full|storage limit", re.I)
_OVERFLOW = re.compile(r"overflow|buffer full", re.I)
_DISCONNECT = re.compile(r"disconnect|lost connection|connection closed|connection failed", re.I)
_RECONNECT = re.compile(r"reconnect|re-establish|connection restored|retry(?:ing)? (?:to )?connect", re.I)
_SOURCE_COMPONENTS = frozenset({"SOURCE_CAPTURE", "SOURCE_UNLOAD", "COMMON"})
_SOURCE_CATEGORIES = {
    "network": ("network", "socket", "connection", "timeout", "refused"),
    "contention": ("lock", "contention", "wait", "blocked"),
    "resource": ("memory", "resource", "limit"),
}
_SOURCE_HINTS = {
    "network": "Network issues detected: check connectivity to the source.",
    "contention": "Database contention: review locks and long transactions on the source.",
    "resource": "Resource limitations: check memory and limits on the source server.",
}


class HealthCollector:
    name = "health"

    def __init__(self) -> None:
        self.sorter_memory = 0
        self.overflow = 0
        self.target_disconnects = 0
        self.source_reconnects = 0
        self.target_reconnects = 0
        self.source_errors = 0
        self.categories: Counter = Counter()
        self.lines: dict[str, list[int]] = {}

    def _mark(self, key: str, line: int) -> None:
        bucket = self.lines.setdefault(key, [])
        if len(bucket) < 5:
            bucket.append(line)

    def feed(self, r: LogRecord) -> None:
        if r.continuation:
            return
        comp, msg = r.component, r.message
        if comp in ("SORTER", "SORTER_STORAGE") and _SORTER_MEMORY.search(msg):
            self.sorter_memory += 1
            self._mark("sorter_memory", r.line_no)
            if _OVERFLOW.search(msg):
                self.overflow += 1
        elif comp in ("TARGET_APPLY", "TARGET_LOAD"):
            if r.is_issue and _DISCONNECT.search(msg):
                self.target_disconnects += 1
                self._mark("target_disconnects", r.line_no)
            elif _RECONNECT.search(msg):
                self.target_reconnects += 1
        elif comp in _SOURCE_COMPONENTS:
            if _RECONNECT.search(msg):
                self.source_reconnects += 1
                self._mark("source_reconnects", r.line_no)
            if r.is_issue:
                self.source_errors += 1
                low = msg.lower()
                for cat, words in _SOURCE_CATEGORIES.items():
                    if any(w in low for w in words):
                        self.categories[cat] += 1

    def result(self) -> dict:
        if self.overflow or self.target_disconnects > 5:
            pipeline = "critical"
        elif self.sorter_memory > 3 or self.target_disconnects:
            pipeline = "warning"
        else:
            pipeline = "healthy"
        if self.categories["network"] > 5 or self.source_reconnects > 5:
            source = "critical"
        elif self.source_errors or self.source_reconnects > 3:
            source = "warning"
        else:
            source = "healthy"
        return {
            "pipeline": {"health": pipeline, "memory_warnings": self.sorter_memory, "overflow_events": self.overflow,
                         "disconnections": self.target_disconnects, "reconnections": self.target_reconnects},
            "source": {"health": source, "errors": self.source_errors, "reconnects": self.source_reconnects,
                       "categories": dict(self.categories),
                       "hints": [_SOURCE_HINTS[c] for c in _SOURCE_CATEGORIES if self.categories[c]]},
            "lines": self.lines,
        }
