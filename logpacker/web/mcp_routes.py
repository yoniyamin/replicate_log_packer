"""HTTP API for managing MCP settings from the web UI.

API keys are stored in ``mcp_config.json`` alongside the job data.
Key changes are written to disk and picked up immediately by the SSE
transport (which re-reads the config on each auth check).
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from logpacker.config import Settings
from logpacker.mcp import clients
from logpacker.mcp.config import MCPConfig

router = APIRouter(prefix="/api/mcp")


def _settings(request: Request) -> Settings:
    return request.app.state.settings


def _base(request: Request) -> str:
    """URL clients should use: ``LOGPACKER_PUBLIC_URL`` if set, else the address this page was opened on.

    The bind address (often ``0.0.0.0`` on a shared server) is never reachable
    from client machines, so it is not used here.
    """
    return _settings(request).public_url.rstrip("/") or str(request.base_url).rstrip("/")


def _load(request: Request) -> MCPConfig:
    return MCPConfig.load(_settings(request).data_dir)


def _save(request: Request, config: MCPConfig) -> None:
    config.save(_settings(request).data_dir)


# ── status ────────────────────────────────────────────────────────────

@router.get("/status")
def mcp_status(request: Request) -> JSONResponse:
    config = _load(request)
    base = _base(request)
    has_keys = bool(config.api_keys)
    return JSONResponse({
        "enabled": has_keys,
        "base_url": base,
        "public_url_set": bool(_settings(request).public_url),
        "mcp_url": clients.mcp_endpoint(base) if has_keys else None,
        "sse_url": f"{base}/mcp-sse/sse" if has_keys else None,
        "keys_count": len(config.api_keys),
    })


# ── API keys ──────────────────────────────────────────────────────────

@router.get("/keys")
def list_keys(request: Request) -> JSONResponse:
    config = _load(request)
    items = []
    for k in config.api_keys:
        items.append({
            "key_hash_prefix": k["key_hash"][:12] + "...",
            "owner": k.get("owner", "unknown"),
        })
    return JSONResponse(items)


class KeyIn(BaseModel):
    owner: str = Field(default="user", min_length=1, max_length=100)


@router.post("/keys")
def create_key(body: KeyIn, request: Request) -> JSONResponse:
    config = _load(request)
    key = config.generate_api_key(body.owner)
    _save(request, config)
    return JSONResponse({
        "key": key,
        "owner": body.owner,
        "configs": clients.all_configs(_base(request), key),
    })


class KeyDeleteIn(BaseModel):
    key_hash_prefix: str


@router.delete("/keys")
def revoke_key(body: KeyDeleteIn, request: Request) -> JSONResponse:
    config = _load(request)
    prefix = body.key_hash_prefix.rstrip(".")
    before = len(config.api_keys)
    config.api_keys = [k for k in config.api_keys if not k["key_hash"].startswith(prefix)]
    if len(config.api_keys) == before:
        raise HTTPException(404, "Key not found.")
    _save(request, config)
    return JSONResponse({"revoked": True})


# ── client config ─────────────────────────────────────────────────────

@router.get("/client-config")
def client_config(request: Request) -> JSONResponse:
    """Return the snippets users paste into Cursor, Claude Code or Claude Desktop.

    Does NOT include an API key — the user should create one first via
    ``POST /api/mcp/keys`` and replace the placeholder.
    """
    return JSONResponse(clients.all_configs(_base(request)))


@router.get("/extension.mcpb")
def download_extension(request: Request) -> Response:
    """Claude Desktop extension with this server's URL pre-filled; the user enters their key on install."""
    return Response(
        clients.build_mcpb(_base(request)),
        media_type="application/zip",
        headers={"Content-Disposition": 'attachment; filename="logpacker.mcpb"'},
    )
