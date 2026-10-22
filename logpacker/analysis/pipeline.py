"""Single pass over the upload: decode -> sanitize -> parse -> collectors.

Writes the sanitized log to disk as it goes; everything after this step
(review, condensing, packing) works only from sanitized data.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from logpacker.analysis.apply import ApplyCollector
from logpacker.analysis.cockpit import build_cockpit
from logpacker.analysis.fileops import FileOpsCollector
from logpacker.analysis.fullload import FullLoadCollector
from logpacker.analysis.health import HealthCollector
from logpacker.analysis.issues import IssuesCollector
from logpacker.analysis.latency import LatencyCollector
from logpacker.analysis.manifest import build_manifest
from logpacker.analysis.metadata import MetadataCollector
from logpacker.analysis.normalize import NORMALIZER_VERSION
from logpacker.analysis.oracle import OracleCollector
from logpacker.analysis.runs import RunsCollector
from logpacker.analysis.signals import SignalsCollector
from logpacker.analysis.stats import StatsCollector
from logpacker.analysis.templates import TemplatesCollector
from logpacker.ingest.lines import LineParser, decode
from logpacker.ingest.sources import detect_kind, iter_sources
from logpacker.sanitize import Sanitizer, SanitizeOptions

ANALYSIS_VERSION = "4"  # bump when collectors or the normalizer change what they report
PROGRESS_EVERY = 20_000
SOURCE_MARKER = "===== LogPacker: file {name} ====="

ProgressFn = Callable[[int, int], None]  # (bytes_done, lines_done)


@dataclass
class JobFiles:
    root: Path

    @property
    def upload(self) -> Path: return self.root / "upload.bin"
    @property
    def sanitized(self) -> Path: return self.root / "sanitized.log"
    @property
    def report(self) -> Path: return self.root / "report.json"
    @property
    def templates(self) -> Path: return self.root / "templates.json"
    @property
    def privacy(self) -> Path: return self.root / "privacy.json"
    @property
    def progress(self) -> Path: return self.root / "progress.json"


def analyze(files: JobFiles, filename: str, options: SanitizeOptions, max_uncompressed: int,
            on_progress: ProgressFn | None = None) -> dict:
    started = time.perf_counter()
    sanitizer = Sanitizer(options)
    parser = LineParser()
    stats, templates, latency, issues = StatsCollector(), TemplatesCollector(), LatencyCollector(), IssuesCollector()
    collectors = [stats, MetadataCollector(), RunsCollector(), issues, latency, SignalsCollector(),
                  ApplyCollector(), FileOpsCollector(), FullLoadCollector(), HealthCollector(), OracleCollector(),
                  templates]
    line_no = decode_errors = 0
    is_archive = detect_kind(files.upload) == "zip"

    with files.upload.open("rb") as raw, files.sanitized.open("w", encoding="utf-8", newline="\n") as out:
        for source in iter_sources(files.upload, raw, max_uncompressed, filename):
            if is_archive:
                line_no += 1
                out.write(SOURCE_MARKER.format(name=sanitizer.apply(source.name)) + "\n")
            for raw_line in source.lines:
                line_no += 1
                text, bad = decode(raw_line)
                decode_errors += bad
                clean = sanitizer.apply(text)
                out.write(clean + "\n")
                record = parser.parse(clean, line_no, source.name)
                for collector in collectors:
                    collector.feed(record)
                if on_progress and line_no % PROGRESS_EVERY == 0:
                    on_progress(raw.tell(), line_no)

    report = {c.name: c.result() for c in collectors if c is not templates}
    report["repetition"] = templates.result()
    report["cockpit"] = build_cockpit(report, latency.samples, issues.timeline)
    report["manifest"] = build_manifest(report)
    report["quality"] = {"decode_errors": decode_errors, "unparsed_lines": parser.unparsed,
                         "normalizer_version": NORMALIZER_VERSION, "analysis_version": ANALYSIS_VERSION}
    report["input"] = {"filename": sanitizer.apply(filename), "bytes": files.upload.stat().st_size,
                       "sanitized_bytes": files.sanitized.stat().st_size,
                       "seconds": round(time.perf_counter() - started, 2)}
    privacy = sanitizer.summary()
    report["privacy"] = _without_examples(privacy)

    _write_json(files.report, report)
    _write_json(files.templates, _template_table(templates))
    # Originals live only in privacy.json for local review; they never enter a package.
    _write_json(files.privacy, {"summary": privacy, "mapping": sanitizer.map.entries()})
    return report


def _without_examples(summary: dict) -> dict:
    categories = [{k: v for k, v in c.items() if k != "examples"} for c in summary["categories"]]
    return {**summary, "categories": categories}


def _template_table(templates: TemplatesCollector) -> dict:
    ids = templates.ids()
    return {key: [ids.get(key, "P----"), count] for key, count in templates.counts.items()}


def _write_json(path: Path, data) -> None:
    path.write_text(json.dumps(data, ensure_ascii=False, default=str), encoding="utf-8")
