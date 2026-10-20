"""Split the log into task runs (start ... stop) so restarts are visible."""

from __future__ import annotations

import re

from logpacker.ingest.lines import LogRecord

_START_RE = re.compile(r"Task Server Log - |Task '[^']+' running .+? mode")
_STOP_RE = re.compile(r"Task is stopp(?:ed|ing)|Task stopped|Stop task", re.I)
_MAX_RUNS = 200


class RunsCollector:
    name = "runs"

    def __init__(self) -> None:
        self.runs: list[dict] = []
        self._last_start_line: int | None = None

    def feed(self, record: LogRecord) -> None:
        if record.continuation:
            return
        msg = record.message
        if ("Task" in msg) and _START_RE.search(msg):
            # The header and the "running ... mode" line belong to one start.
            near_previous = self._last_start_line is not None and record.line_no - self._last_start_line <= 200
            if not near_previous and len(self.runs) < _MAX_RUNS:
                self.runs.append({"run": len(self.runs) + 1, "start_line": record.line_no,
                                  "start_ts": record.ts, "end_line": None, "end_ts": None,
                                  "stop_reason": None})
            self._last_start_line = record.line_no
        elif self.runs and _STOP_RE.search(msg):
            run = self.runs[-1]
            run.update(end_line=record.line_no, end_ts=record.ts, stop_reason=msg[:160])

    def result(self) -> dict:
        return {"count": len(self.runs), "runs": self.runs}
