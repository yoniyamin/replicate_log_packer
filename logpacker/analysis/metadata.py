"""Task identity, endpoints, run mode, settings, key events and log properties."""

from __future__ import annotations

import re

from logpacker.ingest.lines import LogRecord

_HEADER_RE = re.compile(r"Task Server Log - (\S+)\s+\(V([\d.]+)\s+\S+\s+(.+?)(?:,\s+Revision:|,\s+PID:|\))")
_PID_RE = re.compile(r"PID:\s+(\d+)")
_MODE_RE = re.compile(r"Task '([^']+)' running (.+?) in (.+?) mode")
_SOURCE_RE = re.compile(r"Source endpoint '([^']+)'", re.I)
_TARGET_RE = re.compile(r"Target endpoint '([^']+)'", re.I)
_PROVIDER_RE = re.compile(r"using provider\s*(?:syntax\s*)?\(?['\"]([^'\"]+)['\"]", re.I)
_LICENSE_RE = re.compile(r"Licensed to\s+([^,]+),?\s*(.*?)(?:\s*\(at_|,\s*sources:|$)")
_LOGGER_RE = re.compile(r"log level for '([^']+)' has been changed from '(\w+)' to '(\w+)'", re.I)
_STOP_REASON_RE = re.compile(r"Stop reason:\s*(.+?)(?:\s+\(|$)")
_ODBC_RE = re.compile(r"(?:driver|Driver)[:\s]+([^\n;,()]{10,80})")
_TABLE_NAME_RE = re.compile(r"TABLE_NAME='([^']+)'")
_FOR_TABLE_RE = re.compile(r"for table '([^']+)'\.?'([^']+)'")
_FEATURE_EP_RE = re.compile(r"Feature flags for endpoint '([^']+)'")
MAX_EVENTS = 200
MAX_TABLES = 100_000

# (key, label, regex) - first capture group is the value.
_SETTINGS: tuple[tuple[str, str, re.Pattern], ...] = (
    ("bulk_timeout_ms", "Bulk timeout (ms)", re.compile(r"Set Bulk Timeout\s*=\s*(\d+)\s*milliseconds", re.I)),
    ("bulk_timeout_min_ms", "Bulk min timeout (ms)",
     re.compile(r"Set Bulk Timeout Min\s*=\s*(\d+)\s*milliseconds", re.I)),
    ("bulk_max_file_size_mb", "Bulk max file size (MB)", re.compile(r"Bulk max file size:\s*(\d+)\s*MB", re.I)),
    ("parallel_apply_threads", "Parallel apply threads",
     re.compile(r"Parallel bulk apply (?:enabled.*maximum.*?|\()(\d+)", re.I)),
    ("stream_buffer_size", "Stream buffer size", re.compile(r"stream_buffer_size[\"\s:=]+(\d+)", re.I)),
    ("stream_buffers_number", "Stream buffers", re.compile(r"stream_buffers_number[\"\s:=]+(\d+)", re.I)),
    ("stop_on_memory_limit", "Stop reading at memory limit",
     re.compile(r"Stop reading when memory limit reached.*is set to (true|false)", re.I)),
)

# (label, test) for the key-event timeline.
_EVENTS: tuple[tuple[str, re.Pattern], ...] = (
    ("Task initialization completed", re.compile(r"Task initialization completed", re.I)),
    ("Full Load completed", re.compile(r"Full\s+load\s+completed", re.I)),
    ("Transaction consistency reached", re.compile(r"Transaction consistency reached", re.I)),
    ("CDC started", re.compile(r"Starting replication now|Change Data Capture.*started", re.I)),
    ("Task stopped", re.compile(r"Task is stopped|Task stopped", re.I)),
)


class MetadataCollector:
    name = "metadata"

    def __init__(self) -> None:
        self.task = self.version = self.os = self.pid = None
        self.mode = self.start_mode = None
        self.source = self.target = self.source_type = self.target_type = None
        self.license_holder = self.license_type = None
        self.settings: dict[str, dict] = {}
        self.loggers: list[dict] = []
        self.events: list[dict] = []
        self.odbc: list[str] = []
        self.tables: set[str] = set()
        self.rollover = False
        self.closed_at_line: int | None = None
        self.lines = 0
        self.fatal: dict | None = None
        self.flags = {"full_load_completed": False, "cdc_started": False}
        self._feature_endpoints: list[str] = []

    def feed(self, record: LogRecord) -> None:
        self.lines = record.line_no
        if record.continuation:
            return
        msg = record.message
        if "Task Server Log" in msg and self.version is None and (m := _HEADER_RE.search(msg)):
            self.task, self.version, self.os = m.group(1), m.group(2), m.group(3).strip()
            self.pid = (p.group(1) if (p := _PID_RE.search(msg)) else None)
        if "running" in msg and (m := _MODE_RE.search(msg)):
            self.task = self.task or m.group(1)
            self.mode, self.start_mode = m.group(2), m.group(3)
        if "endpoint" in msg:
            self._endpoint(msg)
        if "Feature flags for endpoint" in msg and (m := _FEATURE_EP_RE.search(msg)):
            self._feature_endpoints.append(m.group(1))
        if self.license_holder is None and "Licensed to" in msg and (m := _LICENSE_RE.search(msg)):
            self.license_holder, self.license_type = m.group(1).strip(), m.group(2).strip() or None
        if "log level" in msg and (m := _LOGGER_RE.search(msg)):
            self.loggers.append({"logger": m.group(1), "from": m.group(2), "to": m.group(3), "line": record.line_no})
        for key, label, pattern in _SETTINGS:
            if key not in self.settings and (m := pattern.search(msg)):
                self.settings[key] = {"label": label, "value": m.group(1), "line": record.line_no}
        self._events(record, msg)
        if "rolled over" in msg:
            self.rollover = True
        if "Closing log file" in msg:
            self.closed_at_line = record.line_no
        if self.fatal is None and "fatal error" in msg.lower():
            self.fatal = {"line": record.line_no, "text": msg[:200]}
        if "ODBC" in msg and "river" in msg and len(self.odbc) < 10 and (m := _ODBC_RE.search(msg)):
            driver = m.group(1).strip()
            if driver not in self.odbc:
                self.odbc.append(driver)
        if ("able" in msg or "ABLE" in msg) and len(self.tables) < MAX_TABLES:
            if m := _FOR_TABLE_RE.search(msg):
                self.tables.add(f"{m.group(1)}.{m.group(2)}")
            elif m := _TABLE_NAME_RE.search(msg):
                self.tables.add(m.group(1))

    def _endpoint(self, msg: str) -> None:
        provider = _PROVIDER_RE.search(msg)
        if self.source is None and (m := _SOURCE_RE.search(msg)):
            self.source = m.group(1)
            self.source_type = provider.group(1) if provider else None
        if self.target is None and (m := _TARGET_RE.search(msg)):
            self.target = m.group(1)
            self.target_type = provider.group(1) if provider else None

    def _events(self, record: LogRecord, msg: str) -> None:
        for label, pattern in _EVENTS:
            if pattern.search(msg):
                if label == "Full Load completed":
                    self.flags["full_load_completed"] = True
                if label == "CDC started":
                    self.flags["cdc_started"] = True
                self._event(record, label)
                return
        if "Stop reason" in msg and (m := _STOP_REASON_RE.search(msg)):
            self._event(record, f"Stop reason: {m.group(1)[:120]}")

    def _event(self, record: LogRecord, label: str) -> None:
        if len(self.events) < MAX_EVENTS:
            self.events.append({"line": record.line_no, "ts": record.ts, "event": label})

    def result(self) -> dict:
        closed = self.closed_at_line is not None and self.lines - self.closed_at_line <= 20
        # Fill source/target from feature-flag endpoint names when explicit lines are absent
        if self.source is None and len(self._feature_endpoints) >= 1:
            self.source = self._feature_endpoints[0]
        if self.target is None and len(self._feature_endpoints) >= 2:
            self.target = self._feature_endpoints[1]
        all_eps = [self.source, self.target] + self._feature_endpoints
        is_logstream = any("logstream" in (ep or "").lower() for ep in all_eps)
        return {
            "task": self.task, "version": self.version, "os": self.os, "pid": self.pid,
            "mode": self.mode, "start_mode": self.start_mode,
            "source_endpoint": self.source, "target_endpoint": self.target,
            "source_type": self.source_type, "target_type": self.target_type,
            "is_logstream": is_logstream,
            "license": {"holder": self.license_holder, "type": self.license_type} if self.license_holder else None,
            "settings": self.settings, "loggers": self.loggers[:50],
            "verbose_loggers": sorted({lg["logger"] for lg in self.loggers if lg["to"].upper() in ("TRACE", "VERBOSE")}),
            "key_events": self.events, **self.flags,
            "rollover": self.rollover, "log_closed": closed, "fatal": self.fatal,
            "odbc_drivers": self.odbc, "tables_seen": len(self.tables),
        }
