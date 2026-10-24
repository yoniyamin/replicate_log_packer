"""Full Load lifecycle per table and segment (init -> loading -> complete | error | reloading).

Segments are keyed by (table id, attempt, segment number); a reload starts a new
attempt and resets the table's row counters. Each reload records the error that
caused it and how long the failing segment ran; the SORTER's later "Reload for
table Id N is requested" is matched to it as the moment the reload was executed
(Replicate first lets the segments still running finish). Report shaping is in
fullload_report.py.
"""

from __future__ import annotations

import re
from collections import deque

from logpacker.analysis.fullload_report import build_full_load_report
from logpacker.analysis.timeutil import seconds, span
from logpacker.ingest.lines import LogRecord

_COMPONENTS = frozenset({"TASK_MANAGER", "SOURCE_UNLOAD", "TARGET_LOAD", "TABLES_MANAGER", "SORTER"})
_HINTS = ("initializ", "Start loading", "Unload finished", "Load finished", "Reloading table",
          "Transformation Where", "UNLOAD_TABLE_SEGMENT", "ull load", "ull Load", "target preparation",
          "Reload for table")
_T = r"'([^']+)'\.'([^']+)'\s*\(Id\s*=\s*(\d+)\)"


def _re(p: str) -> re.Pattern:
    return re.compile(p, re.I)


INIT = _re(rf"Start initializing (?:segmented )?table {_T}\s*by subtask\s*(\d+)")
PREP = _re(rf"(?:Completed target preparation|Initialization finished) for (?:segmented )?table {_T}")
START_SEG = _re(rf"Start loading segment #(\d+)\s+of\s+(\d+)\s+of table {_T}\s*by subtask\s*(\d+)")
START_TABLE = _re(rf"Start loading table {_T}\s*by subtask\s*(\d+)")
UNLOAD_SEG = _re(rf"Unload finished for segment #(\d+)\s+of segmented table {_T}\.\s*(\d+)\s+rows sent")
UNLOAD_TABLE = _re(rf"Unload finished for table {_T}\.\s*(\d+)\s+rows sent")
LOAD_SEG = _re(rf"Load finished for segment #(\d+)\s+of segmented table {_T}\.\s*(\d+)\s+rows received\.\s*(\d+)\s+rows skipped\.\s*Volume transferred\s+(\d+)")
LOAD_TABLE = _re(rf"Load finished for table {_T}\.\s*(\d+)\s+rows received\.\s*(\d+)\s+rows skipped\.\s*Volume transferred\s+(\d+)")
TM_SEG = _re(rf"Load finished for segment #(\d+)\s+of table {_T}\s*by subtask\s*\d+\.\s*(\d+)\s+records transferred")
TM_TABLE = _re(rf"Load finished for table {_T}\s*by subtask\s*\d+\.\s*(\d+)\s+records transferred")
RELOAD = _re(r"Reloading table\s+(\d+)\s+because subtask\s*#(\d+)\s+that was loading segment\s+(\d+)\s+finished with error")
RELOAD_REQUEST = _re(r"Reload for table Id\s+(\d+)\s+is requested")
_SOURCE_REF = re.compile(r"\s*\([\w.]+\.\w+:\d+\)\s*$")
WHERE = _re(r"Transformation Where\s+'([^']*)'")
UNLOAD_CMD = _re(r"Command 'UNLOAD_TABLE_SEGMENT'\s+received in component 'st_(\d+)_")
COMPLETED = _re(r"Full\s+load\s+completed")
MODE = _re(r"Task\s+'([^']+)'\s+running full load(?:\s+and\s+CDC)?\s+in\s+(.+?)\s+mode")
MAX_TABLES = 5000


class FullLoadCollector:
    name = "full_load"

    def __init__(self) -> None:
        self.tables: dict[str, dict] = {}
        self.reloads: list[dict] = []
        self.completed = False
        self.mode: str | None = None
        self.first_ts: str | None = None
        self.last_ts: str | None = None
        self._subtask_by_thread: dict[str, str] = {}
        self._recent_errors: deque[tuple[int, str]] = deque(maxlen=20)  # (line, message) for reload causes

    def feed(self, r: LogRecord) -> None:
        if r.continuation or r.component not in _COMPONENTS:
            return
        msg = r.message
        if r.severity == "E":
            self._recent_errors.append((r.line_no, _SOURCE_REF.sub("", msg)))
        if not any(h in msg for h in _HINTS):
            return
        self.first_ts = self.first_ts or r.ts
        self.last_ts = r.ts
        now = seconds(r)
        for handler in (self._init, self._start, self._unload, self._load, self._tm_done, self._reload, self._misc):
            if handler(r, msg, now):
                return

    # --- handlers return True when they consumed the line ------------
    def _init(self, r, msg, now) -> bool:
        if m := INIT.search(msg):
            t = self._table(m.group(1), m.group(2), m.group(3))
            if t is not None:
                t["init_start"] = t["init_start"] or r.ts
                t["_init"] = t["_init"] or now
            return True
        if m := PREP.search(msg):
            if t := self.tables.get(m.group(3)):
                t["init_end"] = r.ts
            return True
        return False

    def _start(self, r, msg, now) -> bool:
        if m := START_SEG.search(msg):
            seg_no, total, schema, name, tid, subtask = m.groups()
        elif m := START_TABLE.search(msg):
            schema, name, tid, subtask = m.groups()
            seg_no, total = "1", "1"
        else:
            return False
        t = self._table(schema, name, tid)
        if t is None:
            return True
        t["segment_count"] = max(t["segment_count"], int(total))
        t["load_start"] = t["load_start"] or r.ts
        t["_load"] = t["_load"] or now
        if t["status"] in ("initializing", "reloading"):
            t["status"] = "loading"
        t["segments"][(t["attempt"], int(seg_no))] = {
            "segment": int(seg_no), "of": int(total), "subtask": subtask, "attempt": t["attempt"],
            "status": "loading", "start_line": r.line_no, "start_ts": r.ts, "_t": now, "_unload": None,
            "rows_sent": None, "rows_received": None, "rows_skipped": None, "volume": None,
            "unload_seconds": None, "gap_seconds": None, "total_seconds": None, "predicate": None, "error": None}
        return True

    def _unload(self, r, msg, now) -> bool:
        if m := UNLOAD_SEG.search(msg):
            seg_no, _, _, tid, rows = m.groups()
        elif m := UNLOAD_TABLE.search(msg):
            _, _, tid, rows = m.groups()
            seg_no = "1"
        else:
            return False
        t, seg = self._segment(tid, int(seg_no))
        if seg is not None:
            seg["_unload"] = now
            seg["unload_seconds"] = span(seg["_t"], now)
            seg["rows_sent"] = int(rows)
            if seg["attempt"] == t["attempt"]:
                t["rows_sent"] += int(rows)
        return True

    def _load(self, r, msg, now) -> bool:
        if m := LOAD_SEG.search(msg):
            seg_no, _, _, tid, rec, skip, vol = m.groups()
        elif m := LOAD_TABLE.search(msg):
            _, _, tid, rec, skip, vol = m.groups()
            seg_no = "1"
        else:
            return False
        t, seg = self._segment(tid, int(seg_no))
        if seg is not None:
            seg.update(rows_received=int(rec), rows_skipped=int(skip), volume=int(vol), end_ts=r.ts,
                       gap_seconds=span(seg["_unload"], now), total_seconds=span(seg["_t"], now))
            if seg["status"] != "error":
                seg["status"] = "complete"
            if seg["attempt"] == t["attempt"]:
                t["rows_received"] += int(rec)
                t["rows_skipped"] += int(skip)
                t["volume"] += int(vol)
            self._refresh(t, r.ts)
        return True

    def _tm_done(self, r, msg, now) -> bool:
        if m := TM_SEG.search(msg):
            t, seg = self._segment(m.group(4), int(m.group(1)))
            if seg is not None:
                seg["records_transferred"] = int(m.group(5))
                seg["total_seconds"] = seg["total_seconds"] or span(seg["_t"], now)
                seg["status"] = "complete" if seg["status"] != "error" else "error"
                self._refresh(t, r.ts)
            return True
        if m := TM_TABLE.search(msg):
            if t := self.tables.get(m.group(3)):
                t["status"], t["load_end"], t["_end"] = "complete", r.ts, now
                t["records_transferred"] = int(m.group(4))
            return True
        return False

    def _reload(self, r, msg, now) -> bool:
        m = RELOAD.search(msg)
        if not m:
            return False
        tid, subtask, seg_no = m.groups()
        t = self.tables.get(tid)
        if t is None:
            return True
        seg = t["segments"].get((t["attempt"], int(seg_no)))
        text = _SOURCE_REF.sub("", msg)
        # The cause is the first error logged after the failing segment started (later ones are the unwind).
        since = seg["start_line"] if seg is not None else 0
        cause = next(((n, m) for n, m in self._recent_errors if n > since), None)
        if seg is not None:
            seg["status"], seg["error"] = "error", text[:300]
        t["reload_count"] += 1
        t["status"] = "reloading"
        t["attempt"] += 1
        for key in ("rows_sent", "rows_received", "rows_skipped", "volume"):
            t[key] = 0
        t["load_start"] = t["load_end"] = None
        t["_load"] = t["_end"] = None
        self.reloads.append({"table": t["table"], "table_id": tid, "subtask": subtask, "segment": int(seg_no),
                             "line": r.line_no, "ts": r.ts, "message": text[:300],
                             "attempt": t["attempt"] - 1,
                             "segment_seconds": span(seg["_t"], now) if seg is not None else None,
                             "cause_line": cause[0] if cause else None, "cause": cause[1][:300] if cause else None,
                             "executed_line": None, "executed_ts": None, "wait_seconds": None, "_t": now})
        self._recent_errors.clear()
        return True

    def _reload_request(self, r, now, tid: str) -> None:
        """The SORTER carrying out the latest reload of this table, once its running segments finished."""
        pending = next((x for x in reversed(self.reloads) if x["table_id"] == tid), None)
        if pending is not None and pending["executed_line"] is None:
            pending.update(executed_line=r.line_no, executed_ts=r.ts, wait_seconds=span(pending["_t"], now))

    def _misc(self, r, msg, now) -> bool:
        if m := RELOAD_REQUEST.search(msg):
            self._reload_request(r, now, m.group(1))
        elif m := UNLOAD_CMD.search(msg):
            self._subtask_by_thread[r.thread] = m.group(1)
        elif r.component == "SOURCE_UNLOAD" and (m := WHERE.search(msg)):
            subtask = self._subtask_by_thread.get(r.thread)
            candidates = [s for t in self.tables.values() for s in t["segments"].values()
                          if s["subtask"] == subtask and s["predicate"] is None]
            if subtask and candidates:
                max(candidates, key=lambda s: s["start_line"])["predicate"] = m.group(1)[:300]
        elif COMPLETED.search(msg):
            self.completed = True
        elif m := MODE.search(msg):
            self.mode = m.group(2)
        return True

    # --- helpers ------------------------------------------------------
    def _table(self, schema: str, name: str, tid: str) -> dict | None:
        t = self.tables.get(tid)
        if t is None:
            if len(self.tables) >= MAX_TABLES:
                return None
            t = self.tables[tid] = {
                "table": f"{schema}.{name}", "id": tid, "status": "initializing", "segment_count": 1,
                "attempt": 1, "reload_count": 0, "rows_sent": 0, "rows_received": 0, "rows_skipped": 0,
                "volume": 0, "init_start": None, "init_end": None, "load_start": None, "load_end": None,
                "_init": None, "_load": None, "_end": None, "segments": {}}
        return t

    def _segment(self, tid: str, seg_no: int) -> tuple[dict | None, dict | None]:
        t = self.tables.get(tid)
        if t is None:
            return None, None
        for attempt in range(t["attempt"], 0, -1):
            if seg := t["segments"].get((attempt, seg_no)):
                return t, seg
        return t, None

    def _refresh(self, t: dict, ts: str | None) -> None:
        current = [s for (a, _), s in t["segments"].items() if a == t["attempt"]]
        loading = any(s["status"] == "loading" for s in current)
        done = sum(1 for s in current if s["status"] == "complete")
        if any(s["status"] == "error" for s in current) and not loading:
            t["status"] = "error"
        elif done >= t["segment_count"] and not loading:
            t["status"], t["load_end"] = "complete", ts
        else:
            t["status"] = "loading"

    def result(self) -> dict:
        return build_full_load_report(self)
