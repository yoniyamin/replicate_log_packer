#!/usr/bin/env python3
"""Delete cached job folders whose analysis version is older than the current one.

Usage:
    python -m tools.clear_stale_cache          # dry-run (default)
    python -m tools.clear_stale_cache --delete  # actually remove stale jobs

Respects LOGPACKER_DATA_DIR if set, otherwise uses ~/.logpacker.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

from logpacker.analysis.pipeline import ANALYSIS_VERSION
from logpacker.config import get_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove stale LogPacker job caches.")
    parser.add_argument("--delete", action="store_true", help="Actually delete stale jobs (default is dry-run).")
    args = parser.parse_args()

    jobs_dir = get_settings().jobs_dir
    if not jobs_dir.exists():
        print(f"Jobs directory does not exist: {jobs_dir}")
        return

    stale: list[tuple[Path, str, str]] = []
    current = 0

    for job_dir in sorted(jobs_dir.iterdir()):
        if not job_dir.is_dir():
            continue
        report = job_dir / "report.json"
        if not report.exists():
            continue
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        version = data.get("quality", {}).get("analysis_version", "?")
        if version == ANALYSIS_VERSION:
            current += 1
        else:
            job_json = job_dir / "job.json"
            filename = "?"
            try:
                filename = json.loads(job_json.read_text(encoding="utf-8")).get("filename", "?")
            except (json.JSONDecodeError, OSError):
                pass
            stale.append((job_dir, version, filename))

    print(f"Current analysis version: {ANALYSIS_VERSION}")
    print(f"Jobs directory:           {jobs_dir}")
    print(f"Up-to-date jobs:          {current}")
    print(f"Stale jobs:               {len(stale)}")
    print()

    if not stale:
        print("Nothing to clean.")
        return

    for job_dir, version, filename in stale:
        tag = f"  {job_dir.name}  v{version}  {filename}"
        if args.delete:
            shutil.rmtree(job_dir, ignore_errors=True)
            print(f"  DELETED  {tag}")
        else:
            print(f"  STALE    {tag}")

    if not args.delete:
        print()
        print("Dry-run complete. Re-run with --delete to remove stale jobs.")


if __name__ == "__main__":
    main()
