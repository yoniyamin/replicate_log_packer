"""HTTP API. Uploads are streamed as a raw request body straight to disk."""

from __future__ import annotations

import json
from itertools import islice

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from logpacker.pack.bundle import build_package
from logpacker.pack.presets import PackOptions
from logpacker.sanitize import SanitizeOptions
from logpacker.web.jobs import Job, JobStore

router = APIRouter(prefix="/api")
PREVIEW_LINES = 80


class SanitizeIn(BaseModel):
    enabled: bool = True
    mask_objects: bool = False
    custom_terms: list[str] = Field(default_factory=list, max_length=200)


class CreateJobIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    sanitize: SanitizeIn = Field(default_factory=SanitizeIn)


class PackIn(BaseModel):
    focus: str = "auto"
    detail: str = "standard"
    attachment: str = "medium"
    question: str = Field(default="", max_length=4000)
    include_findings: bool = True


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _job(request: Request, job_id: str, ready: bool = False) -> Job:
    job = _store(request).get(job_id)
    if job is None:
        raise HTTPException(404, "This log is no longer available. Upload it again.")
    if ready and job.state != "ready":
        raise HTTPException(409, "The log is still being analyzed.")
    return job


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict]:
    return [j.public() for j in _store(request).recent()]


@router.post("/jobs")
def create_job(body: CreateJobIn, request: Request) -> dict:
    limit = _store(request).settings.max_upload_bytes
    if body.size > limit:
        raise HTTPException(413, f"File is larger than the {limit // (1024 * 1024):,} MB limit.")
    terms = [t.strip() for t in body.sanitize.custom_terms if len(t.strip()) >= 2]
    options = SanitizeOptions(body.sanitize.enabled, body.sanitize.mask_objects, terms)
    return _store(request).create(body.filename, body.size, options).public()


@router.put("/jobs/{job_id}/content")
async def upload_content(job_id: str, request: Request) -> dict:
    store = _store(request)
    job = _job(request, job_id)
    if job.state != "created":
        raise HTTPException(409, "Content was already uploaded for this job.")
    store.mark(job, "uploading")
    written = 0
    try:
        with store.files(job_id).upload.open("wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > store.settings.max_upload_bytes:
                    raise HTTPException(413, "Upload exceeded the size limit.")
                fh.write(chunk)
    except HTTPException as exc:
        store.mark(job, "failed", exc.detail)
        raise
    if written == 0:
        store.mark(job, "failed", "The file is empty.")
        raise HTTPException(400, "The file is empty.")
    job.size = written
    store.start_analysis(job)
    return job.public()


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    return _job(request, job_id).public()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request) -> dict:
    _job(request, job_id)
    _store(request).delete(job_id)
    return {"deleted": job_id}


@router.get("/jobs/{job_id}/report")
def job_report(job_id: str, request: Request) -> FileResponse:
    _job(request, job_id, ready=True)
    return FileResponse(_store(request).files(job_id).report, media_type="application/json")


@router.get("/jobs/{job_id}/privacy")
def job_privacy(job_id: str, request: Request) -> FileResponse:
    """Includes original values; served only to this local machine."""
    _job(request, job_id, ready=True)
    return FileResponse(_store(request).files(job_id).privacy, media_type="application/json")


@router.post("/jobs/{job_id}/pack")
def pack(job_id: str, body: PackIn, request: Request) -> dict:
    _job(request, job_id, ready=True)
    try:
        options = PackOptions(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    files = _store(request).files(job_id)
    result = build_package(files, options)
    with (files.root / "pack" / result["attachment"]["file"]).open(encoding="utf-8") as fh:
        preview = [line.rstrip("\n") for line in islice(fh, PREVIEW_LINES)]
    (files.root / "pack" / "last.json").write_text(json.dumps(result["attachment"]), encoding="utf-8")
    result["attachment"] = {**result["attachment"], "preview": preview}
    return result


@router.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str, request: Request):
    _job(request, job_id, ready=True)
    files = _store(request).files(job_id)
    pack_dir = files.root / "pack"
    if kind == "package":
        path, name = files.root / "package.zip", "logpacker_package.zip"
    elif kind == "prompt":
        path, name = pack_dir / "PROMPT.md", "PROMPT.md"
    elif kind == "findings":
        path, name = pack_dir / "findings.json", "findings.json"
    elif kind == "attachment":
        meta = pack_dir / "last.json"
        if not meta.exists():
            raise HTTPException(404, "Build the package first.")
        last = json.loads(meta.read_text(encoding="utf-8"))
        path, name = pack_dir / last["file"], last["name"]
    else:
        raise HTTPException(404, "Unknown download.")
    if not path.exists():
        return JSONResponse({"detail": "Build the package first."}, status_code=404)
    return FileResponse(path, filename=name)
