"""Task identity, endpoints, run mode and task settings visible in the log."""

from __future__ import annotations

import re

from logpacker.ingest.lines import LogRecord

_HEADER_RE = re.compile(r"Task Server Log - (\S+)\s+\(V([\d.]+)")
_MODE_RE = re.compile(r"Task '([^']+)' running (.+?) in (.+?) mode")
_SOURCE_RE = re.compile(r"Source endpoint '([^']+)' is using provider", re.I)
_TARGET_RE = re.compile(r"Target endpoint '([^']+)' is using provider", re.I)

# (key, label, regex) - first capture group is the value.
_SETTINGS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("bulk_timeout_ms", "Bulk timeout (ms)", re.compile(r"Set Bulk Timeout\s*=\s*(\d+)\s*milliseconds", re.I)),
    ("bulk_timeout_min_ms", "Bulk min timeout (ms)",
     re.compile(r"Set Bulk Timeout Min\s*=\s*(\d+)\s*milliseconds", re.I)),
    ("bulk_max_file_size_mb", "Bulk max file size (MB)", re.compile(r"Bulk max file size:\s*(\d+)\s*MB", re.I)),
    ("parallel_apply_threads", "Parallel apply threads",
     re.compile(r"Parallel bulk apply enabled.*maximum.*?(\d+)", re.I)),
    ("stream_buffer_size", "Stream buffer size", re.compile(r"stream_buffer_size[\"\s:=]+(\d+)", re.I)),
    ("stream_buffers_number", "Stream buffers", re.compile(r"stream_buffers_number[\"\s:=]+(\d+)", re.I)),
    ("stop_on_memory_limit", "Stop reading at memory limit",
     re.compile(r"Stop reading when memory limit reached.*is set to (true|false)", re.I)),
)


class MetadataCollector:
    name = "metadata"

    def __init__(self) -> None:
        self.task: str | None = None
        self.version: str | None = None
        self.mode: str | None = None
        self.start_mode: str | None = None
        self.source: str | None = None
        self.target: str | None = None
        self.settings: dict[str, dict] = {}

    def feed(self, record: LogRecord) -> None:
        if record.continuation:
            return
        msg = record.message
        if self.version is None and "Task Server Log" in msg:
            if m := _HEADER_RE.search(msg):
                self.task, self.version = m.group(1), m.group(2)
        if "running" in msg and (m := _MODE_RE.search(msg)):
            self.task = self.task or m.group(1)
            self.mode, self.start_mode = m.group(2), m.group(3)
        if "endpoint" in msg:
            if self.source is None and (m := _SOURCE_RE.search(msg)):
                self.source = m.group(1)
            if self.target is None and (m := _TARGET_RE.search(msg)):
                self.target = m.group(1)
        for key, label, pattern in _SETTINGS:
            if key not in self.settings and (m := pattern.search(msg)):
                self.settings[key] = {"label": label, "value": m.group(1), "line": record.line_no}

    def result(self) -> dict:
        return {
            "task": self.task,
            "version": self.version,
            "mode": self.mode,
            "start_mode": self.start_mode,
            "source_endpoint": self.source,
            "target_endpoint": self.target,
            "settings": self.settings,
        }
