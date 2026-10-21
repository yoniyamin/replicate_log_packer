"""Oracle source: slow archived-redo reads and redo log open/close sessions (trace logging)."""

from __future__ import annotations

import re
from collections import defaultdict

from logpacker.analysis.numbers import describe
from logpacker.analysis.timeutil import seconds, span
from logpacker.ingest.lines import LogRecord

READ_FLOOR_MS = 200
_READ_RE = re.compile(r"Completed to read from archived Redo log.*?(\d+)\s+bytes.*?Read [Tt]ime:\s*(\d+)\s*ms", re.I)
_READ_TIME_RE = re.compile(r"Completed to read from (online|archived) Redo log\s+(\d+)\s+bytes.*?read time is\s+(\d+)\s*ms", re.I)
_LOC_RE = re.compile(r"([a-zA-Z0-9_]+\.c:\d+)")
_OPEN_RE = re.compile(r"Going to open Redo Log with original name '([^']+)'", re.I)
_THREAD_RE = re.compile(r"thread id ['\"](\d+)['\"]", re.I)
_CLOSE_RE = re.compile(r"Close Redo log '([^']+)'", re.I)
MAX_READS = 20_000


class OracleCollector:
    name = "oracle"

    def __init__(self) -> None:
        self.reads: list[tuple] = []  # (bytes, ms, thread, location, line)
        self.read_counts = {"online": 0, "archived": 0}
        self.read_ms = {"online": [], "archived": []}
        self.sessions: list[dict] = []
        self.over_floor = 0
        self._open: dict[str, tuple] = {}

    def feed(self, r: LogRecord) -> None:
        if r.continuation or "Redo" not in r.message:
            return
        msg = r.message
        if "Completed to read" in msg:
            self._read(r, msg)
        elif m := _OPEN_RE.search(msg):
            t = _THREAD_RE.search(msg)
            self._open[m.group(1)] = (seconds(r), r.line_no, r.ts, t.group(1) if t else r.thread)
        elif m := _CLOSE_RE.search(msg):
            if opened := self._open.pop(m.group(1), None):
                took = span(opened[0], seconds(r))
                if took is not None and len(self.sessions) < MAX_READS:
                    path = m.group(1)
                    self.sessions.append({"path": path if len(path) <= 80 else "..." + path[-77:],
                                          "seconds": took, "line": opened[1], "end_line": r.line_no,
                                          "ts": opened[2], "thread": opened[3]})

    def _read(self, r: LogRecord, msg: str) -> None:
        if m := _READ_TIME_RE.search(msg):  # newer format: "... N bytes ... read time is X ms"
            kind, size, ms = m.group(1).lower(), int(m.group(2)), int(m.group(3))
        elif m := _READ_RE.search(msg):
            kind, size, ms = "archived", int(m.group(1)), int(m.group(2))
        else:
            return
        self.read_counts[kind] += 1
        if len(self.read_ms[kind]) < 200_000:
            self.read_ms[kind].append(ms)
        if ms > READ_FLOOR_MS:
            self.over_floor += 1
        if ms > READ_FLOOR_MS and len(self.reads) < MAX_READS:
            loc = _LOC_RE.search(msg)
            self.reads.append((size, ms, r.thread, loc.group(1) if loc else None, r.line_no, kind))

    def result(self) -> dict:
        groups = defaultdict(list)
        for size, ms, thread, loc, line, kind in self.reads:
            groups[(size, thread, loc, kind)].append((ms, line))
        flagged = []
        for (size, thread, loc, kind), samples in groups.items():
            times = [ms for ms, _ in samples]
            if len(times) >= 2 and min(times) > 0 and max(times) / min(times) >= 2:
                flagged.append({"kind": kind, "bytes": size, "thread": thread, "location": loc, "count": len(times),
                                "min_ms": min(times), "max_ms": max(times),
                                "multiplier": round(max(times) / min(times), 1),
                                "lines": [ln for _, ln in sorted(samples, reverse=True)[:5]]})
        flagged.sort(key=lambda g: -g["multiplier"])
        sessions = sorted(self.sessions, key=lambda s: -s["seconds"])
        return {
            "available": bool(self.read_counts["online"] or self.read_counts["archived"] or self.sessions),
            "reads": {kind: {"count": self.read_counts[kind], "ms": describe(self.read_ms[kind], 1)}
                      for kind in ("online", "archived")},
            "slow_reads_over_floor": self.over_floor, "floor_ms": READ_FLOOR_MS,
            "variance_groups": flagged[:15], "has_red_flags": bool(flagged),
            "sessions": {"count": len(self.sessions), "seconds": describe([s["seconds"] for s in self.sessions], 2),
                         "longest": sessions[:5]},
        }
