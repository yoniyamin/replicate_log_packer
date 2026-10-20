"""Condense the sanitized log into an attachment that fits a size target.

What is kept: the header (task settings live there), errors and warnings with a
few lines of context, and the first and last occurrences of every line pattern.
Runs of repetitive lines become one marker line that names the pattern id, so
the model can look it up in findings.json. Each kept line is prefixed with its
original line number: `1234|...`.

Two passes: the first measures every strictness level at once, the second
writes the least strict level that fits.
"""

from __future__ import annotations

import shutil
from collections import Counter, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from logpacker.analysis.pipeline import SOURCE_MARKER
from logpacker.analysis.templates import record_key
from logpacker.ingest.lines import LineParser

_MARKER_PREFIX = SOURCE_MARKER.split("{")[0]
_MARKER_BYTES = 64  # typical size of one "~~ N similar lines omitted ~~" line
_UNKNOWN_TOTAL = 10**12


@dataclass(frozen=True)
class Level:
    keep_first: int
    context: int
    header: int
    issue_cap: int | None = None  # max kept occurrences per error/warning pattern (last one always kept)


# Least to most strict. Only the strict end thins out repeated errors.
LEVELS = (
    Level(5, 5, 400),
    Level(3, 3, 200),
    Level(2, 2, 120, issue_cap=500),
    Level(2, 2, 120, issue_cap=150),
    Level(1, 1, 80, issue_cap=40),
    Level(1, 1, 60, issue_cap=10),
)


@dataclass
class _Line:
    n: int
    text: str
    key: str
    keep: bool


class _Selector:
    """Decides keep/drop for one level. Decisions are delayed by `context` lines
    so an error can pull the lines before it back in."""

    def __init__(self, level: Level, emit: Callable[[_Line], None]):
        self.level, self.emit = level, emit
        self.buffer: deque[_Line] = deque()
        self.keep_until = 0

    def push(self, n: int, text: str, key: str, is_issue: bool, seen: int, total: int) -> None:
        lv = self.level
        keep = (n <= lv.header or n <= self.keep_until or text.startswith(_MARKER_PREFIX)
                or seen <= lv.keep_first or seen == total)
        if is_issue and (lv.issue_cap is None or seen <= lv.issue_cap or seen == total):
            keep = True
            self.keep_until = n + lv.context
            for item in self.buffer:
                item.keep = True
        self.buffer.append(_Line(n, text, key, keep))
        if len(self.buffer) > lv.context:
            self.emit(self.buffer.popleft())

    def flush(self) -> None:
        while self.buffer:
            self.emit(self.buffer.popleft())


class _Meter:
    """Counts the bytes a level would produce without writing anything."""

    def __init__(self) -> None:
        self.bytes = 0
        self._in_gap = False

    def __call__(self, line: _Line) -> None:
        if line.keep:
            if self._in_gap:
                self.bytes += _MARKER_BYTES
            self.bytes += len(line.text.encode("utf-8")) + len(str(line.n)) + 2
        self._in_gap = not line.keep


class _Writer:
    def __init__(self, out, ids: dict[str, list], byte_cap: int | None):
        self.out, self.ids, self.byte_cap = out, ids, byte_cap
        self.bytes = self.kept = self.omitted = self.markers = 0
        self.gap: Counter = Counter()
        self.truncated = False

    def __call__(self, line: _Line) -> None:
        if self.truncated:
            return
        if not line.keep:
            self.gap[line.key] += 1
            return
        self._flush_gap()
        self._write(f"{line.n}|{line.text}\n")
        self.kept += 1

    def close(self) -> None:
        if not self.truncated:
            self._flush_gap()

    def _flush_gap(self) -> None:
        if not self.gap:
            return
        total = sum(self.gap.values())
        key, top = self.gap.most_common(1)[0]
        pid = self.ids.get(key, ["P----"])[0]
        self._write(f"~~ {total:,} similar lines omitted · mostly {pid} ×{top:,} ~~\n")
        self.omitted += total
        self.markers += 1
        self.gap.clear()

    def _write(self, text: str) -> None:
        size = len(text.encode("utf-8"))
        if self.byte_cap is not None and self.bytes + size > self.byte_cap:
            self.out.write("~~ attachment truncated to fit the size limit; see findings.json for the rest ~~\n")
            self.truncated = True
            return
        self.out.write(text)
        self.bytes += size


def condense(sanitized: Path, templates: dict[str, list], out_path: Path, target_bytes: int | None) -> dict:
    if target_bytes is None:
        shutil.copyfile(sanitized, out_path)
        lines = _count_lines(sanitized)
        return {"mode": "full", "lines_in": lines, "lines_kept": lines, "bytes": out_path.stat().st_size,
                "omitted": 0, "markers": 0, "truncated": False, "level": None, "issues_capped": None}

    meters = [_Meter() for _ in LEVELS]
    lines_in = _run(sanitized, templates, [_Selector(lv, m) for lv, m in zip(LEVELS, meters)])
    index = next((i for i, m in enumerate(meters) if m.bytes <= target_bytes), len(LEVELS) - 1)
    level = LEVELS[index]

    with out_path.open("w", encoding="utf-8", newline="\n") as out:
        writer = _Writer(out, templates, target_bytes)
        _run(sanitized, templates, [_Selector(level, writer)])
        writer.close()

    return {"mode": "condensed", "lines_in": lines_in, "level": index + 1, "issues_capped": level.issue_cap,
            "lines_kept": writer.kept, "omitted": writer.omitted, "markers": writer.markers,
            "bytes": out_path.stat().st_size, "truncated": writer.truncated}


def _run(sanitized: Path, templates: dict, selectors: list[_Selector]) -> int:
    seen: Counter = Counter()
    n = 0
    for n, text, key, is_issue in _records(sanitized):
        seen[key] += 1
        total = templates.get(key, (None, _UNKNOWN_TOTAL))[1]
        for selector in selectors:
            selector.push(n, text, key, is_issue, seen[key], total)
    for selector in selectors:
        selector.flush()
    return n


def _records(sanitized: Path) -> Iterator[tuple[int, str, str, bool]]:
    parser = LineParser()
    with sanitized.open(encoding="utf-8") as src:
        for n, raw in enumerate(src, 1):
            text = raw.rstrip("\n")
            record = parser.parse(text, n, "")
            yield n, text, record_key(record), record.is_issue


def _count_lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(chunk.count(b"\n") for chunk in iter(lambda: fh.read(1 << 20), b""))
