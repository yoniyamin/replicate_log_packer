"""Job registry: one folder per analysed log, shared by every session that uploaded it.

States: created -> uploading -> queued -> processing -> ready | failed.
Analysis and packing run in a process pool so sessions work in parallel.
A job is kept for `job_ttl_hours` after its last use, then swept.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import threading
import time
import uuid
from concurrent.futures import Future, ProcessPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path

from logpacker.analysis.pipeline import JobFiles
from logpacker.config import Settings
from logpacker.web import worker

FAILED_TTL_SECONDS = 3600
LEGACY_OWNER = "*"


@dataclass
class Job:
    id: str
    filename: str
    size: int
    sanitize: dict
    owners: list[str] = field(default_factory=list)
    keys: list[str] = field(default_factory=list)
    state: str = "created"
    bytes_done: int = 0
    lines_done: int = 0
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    last_access: float = field(default_factory=time.time)

    def public(self, **extra) -> dict:
        data = {k: v for k, v in asdict(self).items() if k not in ("sanitize", "owners", "keys")}
        data["progress"] = round(self.bytes_done / self.size, 3) if self.size else 0
        return data | extra


class JobStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.root = settings.jobs_dir
        self.root.mkdir(parents=True, exist_ok=True)
        self._jobs: dict[str, Job] = {}
        self._by_key: dict[str, str] = {}
        self._lock = threading.RLock()
        self._pack_locks: dict[str, asyncio.Lock] = {}
        self.pool = ProcessPoolExecutor(max_workers=settings.workers)
        self._load_existing()
        self._stop = threading.Event()
        self._sweeper = threading.Thread(target=self._sweep_loop, name="sweeper", daemon=True)
        self._sweeper.start()

    # --- lookup -------------------------------------------------------
    def create(self, filename: str, size: int, sanitize: dict, owner: str) -> Job:
        job = Job(uuid.uuid4().hex[:12], filename, size, sanitize, owners=[owner])
        self.files(job.id).root.mkdir(parents=True)
        with self._lock:
            self._jobs[job.id] = job
        self._save(job)
        return job

    def get(self, job_id: str, owner: str | None = None) -> Job | None:
        job = self._jobs.get(job_id)
        if job is None or (owner is not None and not self._owned(job, owner)):
            return None
        return job

    @staticmethod
    def _owned(job: Job, owner: str) -> bool:
        return owner in job.owners or LEGACY_OWNER in job.owners

    def touch(self, job: Job) -> None:
        if time.time() - job.last_access > 60:  # avoid rewriting job.json on every poll
            job.last_access = time.time()
            self._save(job)

    def cached(self, key: str) -> Job | None:
        with self._lock:
            job = self._jobs.get(self._by_key.get(key, ""))
        return job if job is not None and job.state == "ready" else None

    def adopt(self, job: Job, owner: str) -> Job:
        """Give another session access to an existing analysis."""
        with self._lock:
            if owner not in job.owners:
                job.owners.append(owner)
            job.last_access = time.time()
        self._save(job)
        return job

    def recent(self, owner: str, limit: int = 10) -> list[Job]:
        jobs = [j for j in self._jobs.values() if self._owned(j, owner) and j.state == "ready"]
        return sorted(jobs, key=lambda j: -j.last_access)[:limit]

    def files(self, job_id: str) -> JobFiles:
        return JobFiles(self.root / job_id)

    def release(self, job: Job, owner: str) -> None:
        """Remove a session's access; delete the files once nobody uses them."""
        with self._lock:
            for name in (owner, LEGACY_OWNER):
                if name in job.owners:
                    job.owners.remove(name)
            orphan = not job.owners
        if orphan:
            self.delete(job.id)
        else:
            self._save(job)

    def delete(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.pop(job_id, None)
            for key in job.keys if job else []:
                self._by_key.pop(key, None)
            self._pack_locks.pop(job_id, None)
        shutil.rmtree(self.root / job_id, ignore_errors=True)

    def add_key(self, job: Job, key: str) -> None:
        with self._lock:
            if key not in job.keys:
                job.keys.append(key)
            if job.state == "ready":
                self._by_key[key] = job.id

    # --- lifecycle ----------------------------------------------------
    def mark(self, job: Job, state: str, error: str | None = None) -> None:
        with self._lock:
            job.state, job.error = state, error
            if state in ("ready", "failed"):
                job.finished_at = time.time()
            if state == "ready":
                for key in job.keys:
                    self._by_key[key] = job.id
        self._save(job)

    def start_analysis(self, job: Job) -> None:
        self.mark(job, "queued")
        future = self.pool.submit(worker.run_analysis, str(self.files(job.id).root), job.filename,
                                  job.sanitize, self.settings.max_uncompressed_bytes)
        future.add_done_callback(lambda f: self._finish(job, f))

    def _finish(self, job: Job, future: Future) -> None:
        try:
            result = future.result()
        except Exception as exc:  # surfaced to the UI as a readable message
            self.mark(job, "failed", str(exc) or exc.__class__.__name__)
            return
        job.bytes_done, job.lines_done = job.size, result["lines"]
        self.mark(job, "ready")

    def live_status(self, job: Job) -> Job:
        """Fold in progress written by the worker process."""
        if job.state in ("queued", "processing"):
            progress = worker.read_progress(self.files(job.id).progress)
            if progress and job.state != "ready":
                job.state = progress.get("state", job.state)
                job.bytes_done = progress.get("bytes_done", job.bytes_done)
                job.lines_done = progress.get("lines_done", job.lines_done)
        return job

    async def pack(self, job: Job, options: dict) -> dict:
        lock = self._pack_locks.setdefault(job.id, asyncio.Lock())
        async with lock:  # one build per job at a time; different jobs run in parallel
            loop = asyncio.get_running_loop()
            return await loop.run_in_executor(self.pool, worker.run_pack, str(self.files(job.id).root), options)

    def close(self) -> None:
        self._stop.set()
        self.pool.shutdown(wait=False, cancel_futures=True)

    # --- retention ----------------------------------------------------
    def sweep(self, now: float | None = None) -> list[str]:
        now = now or time.time()
        ttl = self.settings.job_ttl_hours * 3600
        expired = [j.id for j in list(self._jobs.values())
                   if (j.state == "ready" and now - j.last_access > ttl)
                   or (j.state == "failed" and now - (j.finished_at or j.created_at) > FAILED_TTL_SECONDS)]
        for job_id in expired:
            self.delete(job_id)
        return expired

    def _sweep_loop(self) -> None:
        while not self._stop.wait(self.settings.sweep_minutes * 60):
            self.sweep()

    # --- persistence --------------------------------------------------
    def _save(self, job: Job) -> None:
        path = self.root / job.id / "job.json"
        if not path.parent.exists():
            return
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(asdict(job)), encoding="utf-8")
        os.replace(tmp, path)

    def _load_existing(self) -> None:
        for meta in self.root.glob("*/job.json"):
            try:
                job = Job(**json.loads(meta.read_text(encoding="utf-8")))
            except (ValueError, TypeError):
                shutil.rmtree(meta.parent, ignore_errors=True)
                continue
            if job.state != "ready":  # interrupted by a restart
                shutil.rmtree(meta.parent, ignore_errors=True)
                continue
            if not job.owners:  # saved before sessions existed: visible to every session
                job.owners = [LEGACY_OWNER]
            self._jobs[job.id] = job
            for key in job.keys:
                self._by_key[key] = job.id
        self.sweep()
