"""Staging file operations for cloud targets: compress -> upload, with throughput."""

from __future__ import annotations

import re
from statistics import fmean, pstdev

from logpacker.analysis.numbers import describe, share
from logpacker.analysis.timeutil import seconds, span
from logpacker.ingest.lines import LogRecord

MAX_FILES = 5000
_COMPRESS_RE = re.compile(r"going to compress file '([^']+)'", re.I)
_UPLOAD_RE = re.compile(r"File (\S+) of size (\d+) was uploaded successfully", re.I)
_CSV_RE = re.compile(r"(CDC[0-9A-Fa-f]+\.csv)", re.I)
_TABLE_RE = re.compile(r"bulk_map:.*?'([^']+)'\.+'([^']+)'")


def _file_key(path: str) -> str:
    m = _CSV_RE.search(path)
    return (m.group(1) if m else re.split(r"[\\/]", path)[-1]).lower()


class FileOpsCollector:
    name = "file_ops"

    def __init__(self) -> None:
        self.files: list[dict] = []
        self._open: dict[str, dict] = {}
        self._current: str | None = None
        self._tables: set[str] = set()

    def feed(self, record: LogRecord) -> None:
        if record.continuation:
            return
        msg = record.message
        low = msg.lower()
        if "bulk_map:" in msg and (m := _TABLE_RE.search(msg)):
            self._tables.add(f"{m.group(1)}.{m.group(2)}")
        elif "compress" in low:
            if m := _COMPRESS_RE.search(msg):
                key = _file_key(m.group(1))
                self._open[key] = {"file": key, "line": record.line_no, "ts": record.ts, "_start": seconds(record),
                                   "_compressed": None, "tables": sorted(self._tables)[:5]}
                self._current = key
            elif "file compressed" in low and self._current in self._open:
                self._open[self._current]["_compressed"] = seconds(record)
        elif "uploaded successfully" in low and (m := _UPLOAD_RE.search(msg)):
            self._upload(record, _file_key(m.group(1)), int(m.group(2)))

    def _upload(self, record: LogRecord, key: str, size: int) -> None:
        now = seconds(record)
        op = self._open.pop(key, None) or {"file": key, "line": record.line_no, "ts": record.ts, "_start": now,
                                           "_compressed": None, "tables": sorted(self._tables)[:5]}
        start, compressed = op.pop("_start"), op.pop("_compressed")
        total = span(start, now) or 0.0
        if compressed is not None:
            compress_s, upload_s = span(start, compressed) or 0.0, span(compressed, now) or 0.0
        else:
            compress_s, upload_s = round(total * 0.1, 3), round(total * 0.9, 3)
        op.update(size=size, compress_seconds=compress_s, upload_seconds=upload_s, total_seconds=total,
                  completed_ts=record.ts,
                  throughput_kbps=round(size / 1024 / upload_s, 1) if upload_s > 0 else None)
        self._tables.clear()
        self._current = None
        if len(self.files) < MAX_FILES:
            self.files.append(op)

    def result(self) -> dict:
        files = self.files
        if not files:
            return {"count": 0, "files": [], "insights": []}
        sizes = [f["size"] for f in files]
        uploads = [f["upload_seconds"] for f in files]
        rates = [f["throughput_kbps"] for f in files if f["throughput_kbps"]]
        avg_rate = round(fmean(rates), 1) if rates else None
        for f in files:
            r = f["throughput_kbps"]
            f["throughput_flag"] = (None if not (r and avg_rate) else "slow" if r < avg_rate * .5
                                    else "below_avg" if r < avg_rate * .8 else None)
        return {"count": len(files), "total_bytes": sum(sizes), "size": describe(sizes, 0),
                "upload_seconds": describe(uploads, 3), "compress_seconds": describe([f["compress_seconds"] for f in files], 3),
                "avg_throughput_kbps": avg_rate, "files": files[:2000], "insights": _insights(files, rates)}


def _insights(files: list[dict], rates: list[float]) -> list[dict]:
    if len(files) < 2:
        return []
    out = []
    sizes = [f["size"] for f in files]
    by_size = sorted(files, key=lambda f: f["size"])
    smallest, largest = by_size[0], by_size[-1]
    size_ratio = largest["size"] / smallest["size"] if smallest["size"] else 0
    time_ratio = largest["upload_seconds"] / smallest["upload_seconds"] if smallest["upload_seconds"] else 0
    if size_ratio > 10 and 0 < time_ratio < 2:
        out.append({"severity": "warning", "title": "Fixed latency overhead",
                    "message": f"Files differ {size_ratio:.0f}× in size but upload time only {time_ratio:.1f}×.",
                    "recommendation": "Upload time is dominated by per-file network overhead; larger files (max file size) help."})
    small = sum(1 for s in sizes if s < 10 * 1024)
    if small / len(sizes) > 0.3:
        out.append({"severity": "info", "title": "Many small file uploads",
                    "message": f"{share(small, len(sizes))}% of files are under 10 KB.",
                    "recommendation": "Increase the batch / file size settings so each upload carries more data."})
    if len(files) >= 3 and size_ratio > 5:
        overhead = min(f["upload_seconds"] for f in files)
        effective = [f["size"] / 1024 / (f["upload_seconds"] - overhead) for f in files if f["upload_seconds"] - overhead > 0.1]
        if effective and rates and fmean(effective) > 1.5 * fmean(rates):
            out.append({"severity": "info", "title": "Network overhead",
                        "message": f"About {overhead:.2f}s of every upload is fixed overhead; without it throughput would be "
                                   f"{fmean(effective):,.0f} KB/s instead of {fmean(rates):,.0f} KB/s.",
                        "recommendation": "Check network latency to the staging area."})
    if len(rates) >= 3:
        floor = fmean(rates) - pstdev(rates)
        slow = [f for f in files if f["throughput_kbps"] and f["throughput_kbps"] < floor and f["size"] > 50 * 1024]
        if slow:
            out.append({"severity": "warning", "title": "Throughput anomalies",
                        "message": f"{len(slow)} uploads were well below the usual throughput.",
                        "recommendation": "Look at network or staging-area load around those times."})
    return out
