"""Functions that run inside worker processes (CPU-heavy, so not on API threads).

They take and return only plain, picklable values. Progress is reported through
a small JSON file in the job folder that the API process reads on status calls.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from logpacker.analysis.pipeline import JobFiles, analyze
from logpacker.pack.bundle import build_package
from logpacker.pack.presets import PackOptions
from logpacker.sanitize import SanitizeOptions


def write_progress(path: Path, **data) -> None:
    """Best effort: on Windows the replace fails while the API is reading the file;
    the next update will land instead, so a missed one is harmless."""
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    try:
        tmp.write_text(json.dumps({**data, "at": time.time()}), encoding="utf-8")
        os.replace(tmp, path)
    except OSError:
        tmp.unlink(missing_ok=True)


def read_progress(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_analysis(root: str, filename: str, sanitize: dict, max_uncompressed: int) -> dict:
    files = JobFiles(Path(root))
    write_progress(files.progress, state="processing", bytes_done=0, lines_done=0)

    def progress(done: int, lines: int) -> None:
        write_progress(files.progress, state="processing", bytes_done=done, lines_done=lines)

    report = analyze(files, filename, SanitizeOptions(**sanitize), max_uncompressed, progress)
    files.upload.unlink(missing_ok=True)  # only sanitized data is kept
    try:
        files.progress.unlink(missing_ok=True)
    except OSError:  # the API may be reading it right now (Windows); harmless to leave
        pass
    return {"lines": report["stats"]["lines"]}


def run_pack(root: str, options: dict) -> dict:
    return build_package(JobFiles(Path(root)), PackOptions(**options))
