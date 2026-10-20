"""The contract every collector follows."""

from __future__ import annotations

from typing import Protocol

from logpacker.ingest.lines import LogRecord


class Collector(Protocol):
    name: str

    def feed(self, record: LogRecord) -> None: ...

    def result(self) -> dict: ...
