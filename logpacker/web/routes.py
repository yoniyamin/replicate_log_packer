"""HTTP API. Uploads are streamed as a raw request body straight to disk.

Every route is scoped to the caller's session (cookie set in app.py); a job is
visible only to the sessions that uploaded it.

MCP clients authenticate with ``Authorization: Bearer <api-key>`` instead of
session cookies.  Jobs created via API key use :data:`LEGACY_OWNER` so that
both the web UI and MCP can see them.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from itertools import islice
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field

from logpacker.analysis.pipeline import ANALYSIS_VERSION
from logpacker.kb.related import related_for_job
from logpacker.pack.presets import PackOptions
from logpacker.sanitize import SanitizeOptions
from logpacker.web.cache import content_key, quick_key
from logpacker.web.jobs import Job, JobStore, LEGACY_OWNER

router = APIRouter(prefix="/api")
PREVIEW_LINES = 80
log = logging.getLogger("logpacker.web")

_PARAMS_FILE = Path(__file__).resolve().parent.parent / "data" / "endpoint_params.json"
_endpoint_params: list[dict] | None = None


def _load_endpoint_params() -> list[dict]:
    global _endpoint_params
    if _endpoint_params is None:
        if _PARAMS_FILE.exists():
            _endpoint_params = json.loads(_PARAMS_FILE.read_text(encoding="utf-8"))
        else:
            _endpoint_params = []
    return _endpoint_params


class SanitizeIn(BaseModel):
    enabled: bool = True
    mask_objects: bool = False
    custom_terms: list[str] = Field(default_factory=list, max_length=200)


class CreateJobIn(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=0)
    fingerprint: str | None = Field(default=None, max_length=200)
    sanitize: SanitizeIn = Field(default_factory=SanitizeIn)


class PackIn(BaseModel):
    focus: str = "auto"
    detail: str = "standard"
    attachment: str = "medium"
    question: str = Field(default="", max_length=4000)
    include_findings: bool = True


def _store(request: Request) -> JobStore:
    return request.app.state.jobs


def _is_api_key_auth(request: Request) -> bool:
    """Check if the request carries a valid MCP API key."""
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        return False
    token = auth[7:].strip()
    if not token:
        return False
    try:
        from logpacker.mcp.config import MCPConfig
        settings = request.app.state.settings
        config = MCPConfig.load(settings.data_dir)
        return config.validate_api_key(token)
    except Exception:
        return False


def _session(request: Request) -> str:
    """Return the session id or :data:`LEGACY_OWNER` for API-key callers."""
    if _is_api_key_auth(request):
        return LEGACY_OWNER
    return request.state.session


def _job(request: Request, job_id: str, ready: bool = False) -> Job:
    store = _store(request)
    job = store.get(job_id, _session(request))
    if job is None:
        raise HTTPException(404, "This log is no longer available. Upload it again.")
    if ready:
        if job.state != "ready":
            raise HTTPException(409, "The log is still being analyzed.")
        store.touch(job)
    return job


@router.get("/jobs")
def list_jobs(request: Request) -> list[dict]:
    return [j.public() for j in _store(request).recent(_session(request))]


@router.post("/jobs")
def create_job(body: CreateJobIn, request: Request) -> dict:
    store = _store(request)
    limit = store.settings.max_upload_bytes
    if body.size > limit:
        raise HTTPException(413, f"File is larger than the {limit // (1024 * 1024):,} MB limit.")
    terms = [t.strip() for t in body.sanitize.custom_terms if len(t.strip()) >= 2]
    sanitize = asdict(SanitizeOptions(body.sanitize.enabled, body.sanitize.mask_objects, terms))

    key = quick_key(body.fingerprint, sanitize) if body.fingerprint else None
    if key and (hit := store.cached(key)):
        return store.adopt(hit, _session(request)).public(cached=True)

    job = store.create(body.filename, body.size, sanitize, _session(request))
    if key:
        store.add_key(job, key)
    return job.public(cached=False)


@router.put("/jobs/{job_id}/content")
async def upload_content(job_id: str, request: Request) -> dict:
    store = _store(request)
    job = _job(request, job_id)
    if job.state != "created":
        raise HTTPException(409, "Content was already uploaded for this job.")
    store.mark(job, "uploading")
    written, digest = 0, hashlib.sha256()
    try:
        with store.files(job_id).upload.open("wb") as fh:
            async for chunk in request.stream():
                written += len(chunk)
                if written > store.settings.max_upload_bytes:
                    raise HTTPException(413, "Upload exceeded the size limit.")
                digest.update(chunk)
                fh.write(chunk)
    except HTTPException as exc:
        store.mark(job, "failed", exc.detail)
        raise
    if written == 0:
        store.mark(job, "failed", "The file is empty.")
        raise HTTPException(400, "The file is empty.")

    key = content_key(digest.hexdigest(), job.sanitize)
    if hit := store.cached(key):  # same bytes analysed before, maybe under another name
        log.info("Upload %s (%s, %s bytes) matches cached analysis %s", job.id, job.filename,
                 f"{written:,}", hit.id)
        for k in job.keys:
            store.add_key(hit, k)
        store.delete(job.id)
        return store.adopt(hit, _session(request)).public(cached=True)

    job.size = written
    store.add_key(job, key)
    store.start_analysis(job)
    return job.public(cached=False)


@router.get("/jobs/{job_id}")
def job_status(job_id: str, request: Request) -> dict:
    store = _store(request)
    return store.live_status(_job(request, job_id)).public()


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str, request: Request) -> dict:
    job = _job(request, job_id)
    _store(request).release(job, _session(request))
    return {"deleted": job_id}


@router.get("/jobs/{job_id}/report")
def job_report(job_id: str, request: Request) -> FileResponse:
    _job(request, job_id, ready=True)
    return FileResponse(_store(request).files(job_id).report, media_type="application/json")


@router.get("/jobs/{job_id}/privacy")
def job_privacy(job_id: str, request: Request) -> FileResponse:
    """Includes original values; served only to sessions that uploaded the file."""
    _job(request, job_id, ready=True)
    return FileResponse(_store(request).files(job_id).privacy, media_type="application/json")


@router.get("/jobs/{job_id}/kb")
def job_kb(job_id: str, request: Request) -> dict:
    """Qlik KB articles that may relate to the findings (keyword match; candidates, not answers)."""
    _job(request, job_id, ready=True)
    return related_for_job(_store(request).files(job_id))


@router.post("/jobs/{job_id}/pack")
async def pack(job_id: str, body: PackIn, request: Request) -> dict:
    job = _job(request, job_id, ready=True)
    try:
        PackOptions(**body.model_dump())
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    store = _store(request)
    report = json.loads(store.files(job_id).report.read_text(encoding="utf-8"))
    if report.get("quality", {}).get("analysis_version") != ANALYSIS_VERSION:
        raise HTTPException(409, "This log was analysed by an older version. Upload it again to pack it.")
    result = await store.pack(job, body.model_dump())
    pack_dir = store.files(job_id).root / "pack"
    with (pack_dir / result["attachment"]["file"]).open(encoding="utf-8") as fh:
        preview = [line.rstrip("\n") for line in islice(fh, PREVIEW_LINES)]
    (pack_dir / "last.json").write_text(json.dumps(result["attachment"]), encoding="utf-8")
    result["attachment"] = {**result["attachment"], "preview": preview}
    return result


@router.get("/jobs/{job_id}/download/{kind}")
def download(job_id: str, kind: str, request: Request) -> FileResponse:
    _job(request, job_id, ready=True)
    files = _store(request).files(job_id)
    pack_dir = files.root / "pack"
    if kind == "package":
        path, name = files.root / "package.zip", "logpacker_package.zip"
    elif kind == "prompt":
        path, name = pack_dir / "PROMPT.md", "PROMPT.md"
    elif kind == "findings":
        path, name = pack_dir / "findings.json", "findings.json"
    elif kind == "attachment" and (pack_dir / "last.json").exists():
        last = json.loads((pack_dir / "last.json").read_text(encoding="utf-8"))
        path, name = pack_dir / last["file"], last["name"]
    else:
        raise HTTPException(404, "Build the package first.")
    if not path.exists():
        raise HTTPException(404, "Build the package first.")
    return FileResponse(path, filename=name)


# --- Fetch specific lines from the sanitized log ---

@router.get("/jobs/{job_id}/lines")
def job_lines(job_id: str, n: str, request: Request) -> JSONResponse:
    """Return specific lines from the sanitized log by line number.

    ``n`` is a comma-separated list of 1-based line numbers (max 20).
    Returns a dict mapping each requested line number to its text.
    """
    _job(request, job_id, ready=True)
    try:
        wanted = {int(x) for x in n.split(",") if x.strip()}
    except ValueError:
        raise HTTPException(422, "n must be comma-separated line numbers.")
    if not wanted or len(wanted) > 20:
        raise HTTPException(422, "Provide between 1 and 20 line numbers.")

    path = _store(request).files(job_id).sanitized
    if not path.exists():
        raise HTTPException(404, "Sanitized log not found.")

    result: dict[str, str] = {}
    max_wanted = max(wanted)
    with path.open(encoding="utf-8") as fh:
        for line_no, text in enumerate(fh, 1):
            if line_no in wanted:
                result[str(line_no)] = text.rstrip("\n")
            if line_no >= max_wanted:
                break
    return JSONResponse(result)


# --- Endpoint parameters (from dbpropsV2 data) ---

@router.get("/endpoint-params")
def endpoint_params(q: str | None = None) -> JSONResponse:
    """Return endpoint parameters, optionally filtered by a search query.

    Without ``q``: return the full list (used by the frontend on first load).
    With ``q``: search name/ui_name across all endpoints and return matches.
    """
    data = _load_endpoint_params()
    if not q or len(q.strip()) < 2:
        return JSONResponse(data)

    needle = q.strip().lower()
    results = []
    for ep in data:
        matches = [
            p for p in ep["params"]
            if needle in p.get("name", "").lower() or needle in p.get("ui_name", "").lower()
        ]
        if matches:
            results.append({**ep, "params": matches})
    return JSONResponse(results)
