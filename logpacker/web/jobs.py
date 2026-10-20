"""Job registry: one folder per uploaded log, analysis runs on a worker thread.

States: created -> uploading -> processing -> ready | failed.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from logpacker.analysis.pipeline import JobFiles, analyze
from logpacker.config import Settings
from logpacker.sanitize import SanitizeOptions


@dataclass
class Job:
    id: str
    filename: str
    size: int
    sanitize: dict
    state: str = "created"
    bytes_done: int = 0
    lines_done: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None

    def public(self) -> dict:
        data = asdict(self)
        data.pop("sanitize")
        data["progress"] = round(self.bytes_done / self.size, 3) if self.size else 0
        return data


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.jobs_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="analyze")
        self._load_existing()

    # --- lookup -------------------------------------------------------
    def create(self, filename: str, size: int, sanitize: SanitizeOptions) -> Job:
        job = Job(uuid.uuid4().hex[:12], filename, size, asdict(sanitize))
        self.files(job.id).root.mkdir(parents=True)
        with self._lock:
            self._jobs[job.id] = job
        self._save(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def recent(self, limit: int = 8) -> list[Job]:
        jobs = sorted(self._jobs.values(), key=lambda j: -j.created_at)
        return [j for j in jobs if j.state == "ready"][:limit]

    def files(self, job_id: str) -> JobFiles:
        return JobFiles(self.root / job_id)

    def delete(self, job_id: str) -> None:
        with self._lock:
            self._jobs.pop(job_id, None)
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    # --- lifecycle ----------------------------------------------------
    def mark(self, job: Job, state: str, error: str | None = None) -> None:
        job.state, job.error = state, error
        if state in ("ready", "failed"):
            job.finished_at = time.time()
        self._save(job)

    def start_analysis(self, job: Job) -> None:
        self.mark(job, "processing")
        self._pool.submit(self._run, job)

    def _run(self, job: Job) -> None:
        def progress(done: int, lines: int) -> None:
            job.bytes_done, job.lines_done = done, lines

        try:
            report = analyze(self.files(job.id), job.filename, SanitizeOptions(**job.sanitize),
                             self.settings.max_uncompressed_bytes, progress)
            job.bytes_done, job.lines_done = job.size, report["stats"]["lines"]
            self.files(job.id).upload.unlink(missing_ok=True)  # only sanitized data is kept
            self.mark(job, "ready")
        except Exception as exc:  # surfaced to the UI as a readable message
            self.mark(job, "failed", str(exc) or exc.__class__.__name__)

    # --- persistence --------------------------------------------------
    def _save(self, job: Job) -> None:
        path = self.root / job.id / "job.json"
        if path.parent.exists():
            path.write_text(json.dumps(asdict(job)), encoding="utf-8")

    def _load_existing(self) -> None:
        cutoff = time.time() - self.settings.job_ttl_hours * 3600
        for meta in self.root.glob("*/job.json"):
            try:
                job = Job(**json.loads(meta.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                shutil.rmtree(meta.parent, ignore_errors=True)
                continue
            if job.created_at < cutoff or job.state != "ready":
                shutil.rmtree(meta.parent, ignore_errors=True)
                continue
            self._jobs[job.id] = job
