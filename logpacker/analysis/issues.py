"""Group errors and warnings into patterns and keep a context excerpt for each.

Context is captured during the single pass (ring buffer for lines before,
pending capture for lines after), so nothing ever re-reads the file.
"""

from __future__ import annotations

from collections import Counter, deque
from dataclasses import dataclass, field

from logpacker.analysis.normalize import extract_error_code, fingerprint, normalize
from logpacker.analysis.timeutil import seconds
from logpacker.ingest.lines import LogRecord

CONTEXT_LINES = 4
MAX_GROUPS = 2000
MAX_EXCERPTS = 5
MAX_TIMELINE = 5000
_TABLE_RE_CHARS = "'.'"


@dataclass
class IssueGroup:
    id: str
    severity: str
    component: str | None
    code: str | None
    pattern: str
    count: int = 0
    first_line: int = 0
    last_line: int = 0
    first_ts: str | None = None
    last_ts: str | None = None
    lines: list[int] = field(default_factory=list)
    objects: Counter = field(default_factory=Counter)
    excerpts: list[dict] = field(default_factory=list)
    _first_msg: str | None = field(default=None, repr=False)
    _all_same: bool = field(default=True, repr=False)

    def to_dict(self) -> dict:
        display = self._first_msg if self._all_same and self._first_msg else self.pattern
        return {
            "id": self.id, "severity": self.severity, "component": self.component,
            "code": self.code, "pattern": display, "count": self.count,
            "first_line": self.first_line, "last_line": self.last_line,
            "first_ts": self.first_ts, "last_ts": self.last_ts,
            "sample_lines": self.lines,
            "objects": dict(self.objects.most_common(5)),
            "excerpts": self.excerpts,
        }


class IssuesCollector:
    name = "issues"

    def __init__(self) -> None:
        self.groups: dict[str, IssueGroup] = {}
        self.overflow = 0
        self._before: deque[tuple[int, str]] = deque(maxlen=CONTEXT_LINES)
        self._pending: list[tuple[dict, int]] = []  # (excerpt, lines still to capture)
        self._open: IssueGroup | None = None  # group whose continuation lines we are reading
        self._counts = {"E": 0, "W": 0}
        self.timeline: list[dict] = []

    def feed(self, record: LogRecord) -> None:
        self._capture_after(record)
        if record.is_issue:
            self._add(record)
        elif record.continuation and self._open is not None and record.text.strip():
            self._extend_code(record)
        else:
            self._open = None
        self._before.append((record.line_no, record.text))

    def _add(self, record: LogRecord) -> None:
        code = extract_error_code(record.message)
        pattern = normalize(record.message)
        key = fingerprint(record.severity, record.component, code, pattern)
        group = self.groups.get(key)
        if group is None:
            if len(self.groups) >= MAX_GROUPS:
                self.overflow += 1
                return
            prefix = "E" if record.severity == "E" else "W"
            self._counts[prefix] += 1
            group = IssueGroup(f"{prefix}{self._counts[prefix]:03d}", record.severity,
                               record.component, code, pattern,
                               first_line=record.line_no, first_ts=record.ts)
            group._first_msg = record.message
            self.groups[key] = group
        group.count += 1
        group.last_line, group.last_ts = record.line_no, record.ts
        if group._all_same and record.message != group._first_msg:
            group._all_same = False
        if len(group.lines) < 10:
            group.lines.append(record.line_no)
        if _TABLE_RE_CHARS in record.message:
            for obj in _objects(record.message):
                group.objects[obj] += 1
        if len(self.timeline) < MAX_TIMELINE:
            self.timeline.append({"line": record.line_no, "ts": record.ts, "t": seconds(record),
                                  "severity": record.severity, "component": record.component,
                                  "group": group.id, "text": record.message[:200]})
        if len(group.excerpts) < MAX_EXCERPTS:
            excerpt = {"line": record.line_no, "before": list(self._before),
                       "text": record.text, "after": []}
            group.excerpts.append(excerpt)
            self._pending.append((excerpt, CONTEXT_LINES))
        self._open = group

    def _extend_code(self, record: LogRecord) -> None:
        group = self._open
        if group.code is None:
            group.code = extract_error_code(record.text)

    def _capture_after(self, record: LogRecord) -> None:
        if not self._pending:
            return
        still = []
        for excerpt, remaining in self._pending:
            excerpt["after"].append((record.line_no, record.text))
            if remaining > 1:
                still.append((excerpt, remaining - 1))
        self._pending = still

    def result(self) -> dict:
        ordered = sorted(self.groups.values(), key=lambda g: (g.severity != "E", -g.count))
        return {
            "groups": [g.to_dict() for g in ordered],
            "error_groups": sum(1 for g in ordered if g.severity == "E"),
            "warning_groups": sum(1 for g in ordered if g.severity == "W"),
            "ungrouped_overflow": self.overflow,
            "timeline": [{k: v for k, v in e.items() if k not in ("t", "text")} for e in self.timeline],
        }


def _objects(message: str) -> list[str]:
    out = []
    parts = message.split("'")
    # 'a'.'b' splits into [..., a, '.', b, ...]
    for i in range(1, len(parts) - 2):
        if parts[i + 1] == "." and parts[i] and parts[i + 2]:
            out.append(f"{parts[i]}.{parts[i + 2]}")
    return out[:3]
