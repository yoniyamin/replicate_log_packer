"""Parse the Replicate line prefix: `THREAD: TIMESTAMP [COMPONENT]S: message`.

Lines without a prefix are continuations and inherit the previous line's context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PREFIX_RE = re.compile(
    r"^\s*(\d+):\s+(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?::(\d+))?\s*\[([^\]]+)\]\s*([A-Z])?:?\s?(.*)$"
)

ISSUE_SEVERITIES = frozenset({"E", "W"})


@dataclass(slots=True)
class LogRecord:
    line_no: int
    source: str
    text: str
    ts: str | None
    thread: str | None
    component: str | None
    severity: str | None
    message: str
    continuation: bool
    micro: str | None = None  # sub-second digits after the timestamp, when present

    @property
    def is_issue(self) -> bool:
        return not self.continuation and self.severity in ISSUE_SEVERITIES


class LineParser:
    """Stateful parser: remembers the last prefixed line for continuations."""

    def __init__(self) -> None:
        self._last: LogRecord | None = None
        self.unparsed = 0

    def parse(self, text: str, line_no: int, source: str) -> LogRecord:
        match = _PREFIX_RE.match(text)
        if match:
            thread, ts, micro, component, severity, message = match.groups()
            record = LogRecord(line_no, source, text, ts, thread, component.strip(),
                               severity, message.strip(), False, micro)
            self._last = record
            return record

        last = self._last
        if last is None:
            self.unparsed += 1
        return LogRecord(
            line_no, source, text,
            last.ts if last else None,
            last.thread if last else None,
            last.component if last else None,
            last.severity if last else None,
            text.strip(), True,
            last.micro if last else None,
        )


def decode(raw: bytes) -> tuple[str, bool]:
    """Decode one raw line; returns (text without line ending, had_decode_errors)."""
    try:
        return raw.decode("utf-8").rstrip("\r\n"), False
    except UnicodeDecodeError:
        return raw.decode("utf-8", errors="replace").rstrip("\r\n"), True
