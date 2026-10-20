"""Open an uploaded file (.log, .gz or .zip) as one or more streams of byte lines.

Type is detected from magic bytes, not the file name. Zip members are streamed,
never extracted to disk, and the total uncompressed size is capped.
"""

from __future__ import annotations

import gzip
import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterator

_TS_RE = re.compile(rb"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")
_MAX_RATIO = 400  # reject zip members that claim absurd compression ratios


class SourceError(ValueError):
    """The upload cannot be read as a Replicate log."""


@dataclass
class LogSource:
    name: str
    lines: Iterator[bytes]


class SizeGuard:
    """Counts uncompressed bytes and stops when the limit is exceeded."""

    def __init__(self, limit: int):
        self.limit = limit
        self.total = 0

    def wrap(self, lines: Iterator[bytes]) -> Iterator[bytes]:
        for line in lines:
            self.total += len(line)
            if self.total > self.limit:
                raise SourceError("Uncompressed content is larger than the configured limit")
            yield line


def detect_kind(path: Path) -> str:
    with path.open("rb") as fh:
        head = fh.read(4)
    if head[:2] == b"\x1f\x8b":
        return "gzip"
    if head == b"PK\x03\x04":
        return "zip"
    return "text"


def iter_sources(path: Path, raw: BinaryIO, max_uncompressed: int,
                 display_name: str | None = None) -> Iterator[LogSource]:
    """Yield each log stream lazily (consume one before asking for the next).

    `raw` is the open upload; its position is used for progress.
    """
    guard = SizeGuard(max_uncompressed)
    name = display_name or path.name
    kind = detect_kind(path)
    if kind == "gzip":
        stream = io.BufferedReader(gzip.GzipFile(fileobj=raw))
        yield LogSource(name.removesuffix(".gz"), guard.wrap(iter(stream)))
    elif kind == "zip":
        yield from _zip_sources(raw, guard)
    else:
        yield LogSource(name, guard.wrap(iter(raw)))


def _zip_sources(raw: BinaryIO, guard: SizeGuard) -> Iterator[LogSource]:
    try:
        archive = zipfile.ZipFile(raw)
    except zipfile.BadZipFile as exc:
        raise SourceError("The zip file is damaged or not a zip") from exc

    members = [m for m in archive.infolist() if _is_log_member(m)]
    if not members:
        raise SourceError("No .log files were found inside the zip")
    for member in members:
        if member.compress_size and member.file_size / member.compress_size > _MAX_RATIO:
            raise SourceError(f"Zip member {member.filename} has a suspicious compression ratio")

    for member in sorted(members, key=lambda m: (_first_timestamp(archive, m), m.filename)):
        with archive.open(member) as fh:
            yield LogSource(Path(member.filename).name, guard.wrap(iter(io.BufferedReader(fh))))


def _is_log_member(member: zipfile.ZipInfo) -> bool:
    name = member.filename.lower()
    return not member.is_dir() and (name.endswith(".log") or ".log." in name)


def _first_timestamp(archive: zipfile.ZipFile, member: zipfile.ZipInfo) -> bytes:
    """Rolled-over logs are ordered by their first timestamp, so peek at the head."""
    with archive.open(member) as fh:
        match = _TS_RE.search(fh.read(8192))
    return match.group(0) if match else b"9999"
