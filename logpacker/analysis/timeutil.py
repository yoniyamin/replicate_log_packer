"""Timestamp helpers. Log timestamps are local and naive; only differences matter."""

from __future__ import annotations

from datetime import datetime
from functools import lru_cache

from logpacker.ingest.lines import LogRecord


@lru_cache(maxsize=8192)
def epoch(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


def seconds(record: LogRecord) -> float | None:
    """Record time in seconds, including sub-second digits when the log has them."""
    if not record.ts:
        return None
    base = epoch(record.ts)
    if record.micro:
        base += int(record.micro) / 10 ** len(record.micro)
    return base


def span(start: float | None, end: float | None) -> float | None:
    if start is None or end is None:
        return None
    return round(max(0.0, end - start), 3)
