"""Runtime settings, read once from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_MB = 1024 * 1024


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    max_upload_bytes: int
    max_uncompressed_bytes: int
    job_ttl_hours: int
    host: str
    port: int

    @property
    def jobs_dir(self) -> Path:
        return self.data_dir / "jobs"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    data_dir = Path(os.environ.get("LOGPACKER_DATA_DIR", Path.home() / ".logpacker"))
    return Settings(
        data_dir=data_dir,
        max_upload_bytes=_int_env("LOGPACKER_MAX_UPLOAD_MB", 4096) * _MB,
        max_uncompressed_bytes=_int_env("LOGPACKER_MAX_UNCOMPRESSED_MB", 16384) * _MB,
        job_ttl_hours=_int_env("LOGPACKER_JOB_TTL_HOURS", 72),
        host=os.environ.get("LOGPACKER_HOST", "127.0.0.1"),
        port=_int_env("LOGPACKER_PORT", 8765),
    )
