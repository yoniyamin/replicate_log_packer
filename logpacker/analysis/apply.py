"""TARGET_APPLY: batches, per-table apply statistics, one-by-one episodes, MERGE use.

Replicate logs come in two orderings, both handled here:
  A) Finish bulk because X -> Bulk finished. -> Going to start applying -> statements -> Finished applying bulk changes
  B) Going to start applying -> statements -> Finish bulk because X -> Bulk finished.
A batch runs from "Going to start applying" (or the first statement) until
"Finished applying bulk changes", "Bulk finished." or the next batch start.
A close reason seen while no batch is open belongs to the next batch.
Derived statistics live in apply_report.py.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from logpacker.analysis.apply_report import build_apply_report
from logpacker.analysis.timeutil import seconds, span
from logpacker.ingest.lines import LogRecord

MAX_BATCHES = 50_000
MAX_EPISODES = 500
MAX_TABLE_OPS = 20_000

_START_RE = re.compile(r"Going to start applying bulk changes", re.I)
_END_RE = re.compile(r"Finished applying bulk changes|Bulk finished\.", re.I)
_STATEMENT_RE = re.compile(r"Going to run (\w+) statement.*?from seq (\d+) to seq (\d+)", re.I)
_TABLE_START_RE = re.compile(r"Start applying (?:of )?'([A-Z_]+) \(\d+\)'.*?for table '([^']+)'\.?'([^']+)'", re.I)
_TABLE_DONE_RE = re.compile(r"Finished applying of (\d+) '([A-Z_]+) \(\d+\)' events for table '([^']+)'\.?'([^']+)'", re.I)
_OBO_RE = re.compile(r"Applying\s+\S+\s+one-by-one for table\s+'([^']+)'\.?'([^']+)'", re.I)
_BACK_RE = re.compile(r"Switch back to bulk apply mode", re.I)
_NO_PK_RE = re.compile(r"no PK for table|without.*primary key", re.I)
_NO_BULK_RE = re.compile(r"Apply no Bulk", re.I)
_MERGE_RE = re.compile(r"Going to execute MERGE|Merge table statement MERGE", re.I)
_TABLE_RE = re.compile(r"'([^']+)'\.+'([^']+)'")
# Logged against the TARGET table name, which may differ from the source name
# (renamed by a transformation), e.g. "Primary key ID used for table 'ODS.T_COPY'".
_PK_USED_RE = re.compile(r"(?:Primary key|Unique index)\s+(.+?)\s+used for table\s+'([^']+)'", re.I)
# First "SCHEMA"."TABLE" in a statement that is not Replicate's own changes table.
_QUOTED_TABLE_RE = re.compile(r'"([^"]+)"\."((?!attrep_)[^"]+)"', re.I)
_PK_RES = (
    ("PKi", re.compile(r"same bulk.*INSERT.*same PK|INSERT.*changes PK.*same bulk", re.I)),
    ("PKu", re.compile(r"same bulk.*Update.*updated.*same PK|Update.*changes PK.*same bulk", re.I)),
    ("PKd", re.compile(r"same bulk.*deleted.*same PK|DELETE.*changes PK.*same bulk", re.I)),
)


def classify_close_reason(text: str) -> str:
    for code, pattern in _PK_RES:
        if pattern.search(text):
            return code
    low = text.lower()
    for needle, code in (("memory", "MEM"), ("stream timeout", "TIM"), ("bulk timeout", "TMO"),
                         ("tables with pk", "SNG"), ("single", "SNG"), ("resume", "RES"),
                         ("start_load_table", "LOAD")):
        if needle in low:
            return code
    return "Normal"


@dataclass
class _Batch:
    line: int
    ts: str | None
    t: float | None
    reason: str | None = None
    changes: int = 0
    statements: int = 0
    tables: set = field(default_factory=set)


@dataclass
class TableStats:
    name: str
    ops: dict = field(default_factory=lambda: {"INSERT": 0, "UPDATE": 0, "DELETE": 0, "MERGE": 0})
    applies: int = 0
    apply_time: float = 0.0
    max_apply_time: float = 0.0
    one_by_one: int = 0
    errors: int = 0
    has_pk: bool | None = None
    pk_columns: str | None = None
    target: str | None = None
    first_line: int = 0


class ApplyCollector:
    name = "apply"

    def __init__(self) -> None:
        self.batches: list[dict] = []
        self.batch_overflow = 0
        self.tables: dict[str, TableStats] = {}
        self.table_ops: list[dict] = []
        self.episodes: list[dict] = []
        self.merge_seen = False
        self.no_bulk = 0
        self.no_pk = 0
        self._batch: _Batch | None = None
        self._pending_reason: str | None = None
        self._open_applies: dict[tuple[str, str], tuple[float | None, int]] = {}
        self._episode: dict | None = None
        self._pk_by_target: dict[str, str] = {}   # target table -> key columns
        self._target_of: dict[str, str] = {}      # source table -> target table
        self._applying: str | None = None         # source table whose statements are running

    def feed(self, record: LogRecord) -> None:
        if record.continuation:
            return
        msg = record.message
        if self._episode is not None and "Failed to execute" in msg:
            self._episode["failed_executions"] += 1
        if record.component != "TARGET_APPLY":
            return
        if record.is_issue:
            self._issue(record)

        if "bulk" in msg or "Bulk" in msg or "same PK" in msg or "changes PK" in msg:
            self._bulk_line(record, msg)
        if "used for table" in msg and (m := _PK_USED_RE.search(msg)):
            self._pk_by_target[m.group(2)] = m.group(1)
        if "Going to run" in msg and (m := _STATEMENT_RE.search(msg)):
            batch = self._batch or self._open(record)
            batch.changes += int(m.group(3)) - int(m.group(2)) + 1
            batch.statements += 1
            if self._applying and self._applying not in self._target_of and (t := _QUOTED_TABLE_RE.search(msg)):
                self._target_of[self._applying] = f"{t.group(1)}.{t.group(2)}"
        elif "applying" in msg:
            self._table_line(record, msg)
        if "one-by-one" in msg:
            self._one_by_one(record, msg)
        if _MERGE_RE.search(msg):
            self.merge_seen = True
        if _NO_BULK_RE.search(msg):
            self.no_bulk += 1
        if _NO_PK_RE.search(msg):
            self.no_pk += 1
            if m := _TABLE_RE.search(msg):
                self._table(f"{m.group(1)}.{m.group(2)}", record).has_pk = False

    # --- batches ------------------------------------------------------
    def _bulk_line(self, record: LogRecord, msg: str) -> None:
        if "Finish bulk because" in msg or "Finish Bulk because" in msg or "same bulk" in msg:
            reason = classify_close_reason(msg)
            if self._batch is not None and self._batch.reason is None:
                self._batch.reason = reason
            else:
                self._pending_reason = reason
        if "tables with PK" in msg and self._batch is not None:
            self._batch.reason = self._batch.reason or "SNG"
        if _is_batch_start(msg):
            if self._batch is not None and self._batch.statements:
                self._close(record)
            self._open(record)
        elif _END_RE.search(msg) and self._batch is not None and self._batch.statements:
            self._close(record)
        elif "Switch back" in msg and _BACK_RE.search(msg):
            self._end_episode(record)

    def _open(self, record: LogRecord) -> _Batch:
        self._batch = _Batch(record.line_no, record.ts, seconds(record), self._pending_reason)
        self._pending_reason = None
        return self._batch

    def _close(self, record: LogRecord) -> None:
        b = self._batch
        self._batch = None
        if len(self.batches) >= MAX_BATCHES:
            self.batch_overflow += 1
            return
        self.batches.append({
            "line": b.line, "end_line": record.line_no, "ts": b.ts,
            "duration": span(b.t, seconds(record)), "changes": b.changes, "statements": b.statements,
            "reason": b.reason or "Normal", "tables": sorted(b.tables)[:5],
        })

    # --- per table ----------------------------------------------------
    def _table(self, name: str, record: LogRecord) -> TableStats:
        stats = self.tables.get(name)
        if stats is None:
            stats = self.tables[name] = TableStats(name, first_line=record.line_no)
        return stats

    def _table_line(self, record: LogRecord, msg: str) -> None:
        if m := _TABLE_START_RE.search(msg):
            op, name = _op(m.group(1)), f"{m.group(2)}.{m.group(3)}"
            self._table(name, record)
            self._applying = name
            self._open_applies[(name, op)] = (seconds(record), record.line_no)
            if self._batch is not None:
                self._batch.tables.add(name)
        elif m := _TABLE_DONE_RE.search(msg):
            count, op, name = int(m.group(1)), _op(m.group(2)), f"{m.group(3)}.{m.group(4)}"
            stats = self._table(name, record)
            stats.ops[op] += count
            started, start_line = self._open_applies.pop((name, op), (None, record.line_no))
            took = span(started, seconds(record))
            if took is not None:
                stats.applies += 1
                stats.apply_time += took
                stats.max_apply_time = max(stats.max_apply_time, took)
            if len(self.table_ops) < MAX_TABLE_OPS:
                self.table_ops.append({"table": name, "op": op, "rows": count, "line": start_line,
                                       "ts": record.ts, "seconds": took})
            if self._batch is not None:
                self._batch.tables.add(name)

    # --- one-by-one ---------------------------------------------------
    def _one_by_one(self, record: LogRecord, msg: str) -> None:
        m = _OBO_RE.search(msg)
        if not m:
            return
        name = f"{m.group(1)}.{m.group(2)}"
        self._table(name, record).one_by_one += 1
        if self._episode is not None and self._episode["table"] == name:
            return
        self._end_episode(record)
        if len(self.episodes) < MAX_EPISODES:
            self._episode = {"table": name, "start_line": record.line_no, "start_ts": record.ts,
                             "_t": seconds(record), "end_line": None, "end_ts": None,
                             "seconds": None, "failed_executions": 0}

    def _end_episode(self, record: LogRecord) -> None:
        ep = self._episode
        if ep is None:
            return
        self._episode = None
        ep.update(end_line=record.line_no, end_ts=record.ts, seconds=span(ep.pop("_t"), seconds(record)))
        self.episodes.append(ep)

    def _issue(self, record: LogRecord) -> None:
        if m := _TABLE_RE.search(record.message):
            self._table(f"{m.group(1)}.{m.group(2)}", record).errors += 1

    def result(self) -> dict:
        if self._episode is not None:  # still in one-by-one mode at end of log
            ep = self._episode
            ep.pop("_t", None)
            self.episodes.append(ep)
            self._episode = None
        self._resolve_keys()
        return build_apply_report(self)

    def _resolve_keys(self) -> None:
        """Attach key info logged under a target name to the source table that applies into it."""
        for name, stats in self.tables.items():
            target = self._target_of.get(name, name)
            stats.target = target if target != name else None
            cols = self._pk_by_target.get(target)
            if cols and stats.has_pk is None:
                stats.has_pk, stats.pk_columns = True, cols


def _is_batch_start(msg: str) -> bool:
    return "Going to start applying" in msg and bool(_START_RE.search(msg))


def _op(name: str) -> str:
    name = name.upper()
    return name if name in ("INSERT", "UPDATE", "DELETE") else "MERGE"
