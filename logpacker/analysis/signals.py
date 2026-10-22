"""Known Replicate behaviours worth pointing out, defined as data.

To add a signal, append a Signal to SIGNALS. `components` and `needle` are
cheap filters checked before the regex; `issues_only` limits matching to
error/warning lines (useful for words like "timeout" that also appear in settings).
If the regex has a capture group, its values are tallied (e.g. table names).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from logpacker.ingest.lines import LogRecord


@dataclass(frozen=True)
class Signal:
    id: str
    label: str
    area: str
    pattern: re.Pattern
    hint: str
    needle: str | None = None
    components: frozenset[str] | None = None
    issues_only: bool = False


def _re(p: str) -> re.Pattern:
    return re.compile(p, re.IGNORECASE)


SIGNALS: tuple[Signal, ...] = (
    Signal("one_by_one", "Apply fell back to one-by-one mode", "apply",
           _re(r"one-by-one for table\s+(\S+)"),
           "Batch apply failed for a table and Replicate retried row by row; usually slow and data-related.",
           needle="one-by-one"),
    Signal("bulk_back", "Returned to bulk apply mode", "apply", _re(r"Switch back to bulk apply mode"),
           "Recovery after a one-by-one episode.", needle="bulk apply"),
    Signal("batch_close_reason", "Batch closed early", "apply",
           _re(r"Finish [Bb]ulk because(?: of)?\s*(.+?)(?:\s*\(|$)"),
           "Why batches were closed; frequent PK or memory reasons shrink batch size.", needle="because"),
    Signal("no_pk", "Table without primary key", "apply", _re(r"no PK for table|Apply no Bulk|without.*primary key"),
           "Tables without PK cannot use efficient batch apply.", needle="pk"),
    Signal("merge", "MERGE statements executed", "apply", _re(r"Going to execute MERGE|Merge table statement MERGE"),
           "Target uses MERGE-based apply (common for cloud warehouses).", needle="merge"),
    Signal("target_disconnect", "Target connection lost", "connectivity",
           _re(r"disconnect|lost connection|connection closed|connection failed"),
           "Target connectivity drops cause apply retries and latency.",
           components=frozenset({"TARGET_APPLY", "TARGET_LOAD"}), issues_only=True),
    Signal("network", "Network or timeout error", "connectivity",
           _re(r"network error|socket error|timed? ?out|connection refused|connection reset"),
           "Network instability or a too-short timeout setting.", issues_only=True),
    Signal("reconnect", "Reconnect / retry attempts", "connectivity",
           _re(r"reconnect|re-establish|retry(?:ing)? (?:to )?connect"), "Replicate is recovering a connection."),
    Signal("memory", "Memory pressure", "resources",
           _re(r"memory limit|out of memory|memory consumption|buffer full|storage limit|swap"),
           "Large transactions or too little memory; sorter may offload to disk.", issues_only=True),
    Signal("resource_limit", "Resource limit reached", "resources",
           _re(r"resource limit|thread limit|max connections|disk full|no space left"),
           "An OS or database resource is exhausted.", issues_only=True),
    Signal("table_reload", "Table reload requested", "full_load", _re(r"Reload for table Id (\d+) is requested"),
           "Tables were reloaded during CDC.", needle="reload"),
    Signal("fl_table_done", "Full Load table finished", "full_load",
           _re(r"Load finished for table '([^']+'\.'[^']+)'"), "Tables completed by Full Load.",
           needle="load finished"),
    Signal("fl_completed", "Full Load completed", "full_load", _re(r"Full\s+Load\s+completed"),
           "The Full Load phase finished.", needle="full"),
    Signal("fl_segment_error", "Full Load segment failed and reloaded", "full_load",
           _re(r"Reloading table\s+\d+\s+because subtask"), "A parallel-load segment failed and was restarted.",
           needle="reloading"),
    Signal("redo_read", "Oracle archived redo read", "source",
           _re(r"Completed to read from archived Redo log"), "Source is reading archived redo (possible lag).",
           needle="redo"),
    Signal("file_upload", "Files uploaded to target staging", "target_files",
           _re(r"File \S+ of size \d+ was uploaded successfully"), "Cloud target staging uploads.",
           needle="uploaded"),
    Signal("assertion", "ASSERTION warnings", "data", _re(r".+"),
           "Replicate detected a data anomaly that may cause replication issues.",
           components=frozenset({"ASSERTION"})),
    Signal("logstream_missing_files", "Missing Logstream audit files", "source",
           _re(r"Audit files .+ are missing"),
           "Logstream audit files are missing. The Logstream source task must catch up or resend the missing files before replication can continue without data gaps.",
           needle="missing", components=frozenset({"SOURCE_CAPTURE"})),
)


class SignalsCollector:
    name = "signals"

    def __init__(self, signals: tuple[Signal, ...] = SIGNALS) -> None:
        self.signals = signals
        self.hits: dict[str, dict] = {}

    def feed(self, record: LogRecord) -> None:
        if record.continuation:
            return
        lower = None
        for sig in self.signals:
            if sig.issues_only and not record.is_issue:
                continue
            if sig.components is not None and record.component not in sig.components:
                continue
            if sig.needle is not None:
                lower = lower or record.message.lower()
                if sig.needle not in lower:
                    continue
            match = sig.pattern.search(record.message)
            if match:
                self._hit(sig, record, match)

    def _hit(self, sig: Signal, record: LogRecord, match: re.Match) -> None:
        hit = self.hits.get(sig.id)
        if hit is None:
            hit = self.hits[sig.id] = {"id": sig.id, "label": sig.label, "area": sig.area, "hint": sig.hint,
                                       "count": 0, "first_line": record.line_no, "lines": [],
                                       "values": Counter()}
        hit["count"] += 1
        hit["last_line"] = record.line_no
        if len(hit["lines"]) < 5:
            hit["lines"].append(record.line_no)
        if match.groups() and match.group(1):
            hit["values"][match.group(1).strip()[:120]] += 1

    def result(self) -> dict:
        out = []
        for hit in sorted(self.hits.values(), key=lambda h: -h["count"]):
            out.append({**hit, "values": dict(hit["values"].most_common(8))})
        return {"signals": out}
