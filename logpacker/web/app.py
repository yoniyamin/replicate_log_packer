"""FastAPI application factory."""

from __future__ import annotations

import logging
import secrets
from contextlib import asynccontextmanager
from http.cookies import SimpleCookie
from pathlib import Path

from fastapi import FastAPI, Request
from starlette.types import ASGIApp, Receive, Scope, Send

from logpacker import __version__
from logpacker.config import Settings, get_settings
from logpacker.web.assets import mount_assets
from logpacker.web.jobs import JobStore
from logpacker.web.mcp_routes import router as mcp_router
from logpacker.web.routes import router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_COOKIE = "lp_session"
SESSION_MAX_AGE = 30 * 24 * 3600

log = logging.getLogger("logpacker.web")


class _SessionMiddleware:
    """Pure ASGI middleware that assigns a session cookie to each browser.

    MCP paths are passed through **without any wrapping** so the raw ASGI
    protocol used by the MCP transports is never disturbed.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        if path.startswith(("/mcp", "/mcp-sse")):
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        cookie_header = headers.get(b"cookie", b"").decode("latin-1")
        cookies = SimpleCookie(cookie_header)
        session_morsel = cookies.get(SESSION_COOKIE)
        existing = session_morsel.value if session_morsel else None
        fresh = not existing or len(existing) != 32
        session = secrets.token_hex(16) if fresh else existing

        scope.setdefault("state", {})
        scope["state"]["session"] = session

        if not fresh:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message: dict) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                cookie_val = (
                    f"{SESSION_COOKIE}={session}; "
                    f"Max-Age={SESSION_MAX_AGE}; "
                    f"HttpOnly; SameSite=Strict; Path=/"
                )
                headers.append((b"set-cookie", cookie_val.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cookie)


class _ApiKeyGuard:
    """Pure ASGI wrapper that rejects MCP requests without a valid ``Authorization: Bearer`` key.

    The config is re-read on every request so keys created or revoked in the
    web UI take effect immediately (and revoking a key cuts off new requests).
    """

    def __init__(self, app: ASGIApp, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope.get("method") == "OPTIONS":
            await self.app(scope, receive, send)
            return

        from logpacker.mcp.config import MCPConfig

        auth = dict(scope.get("headers", [])).get(b"authorization", b"").decode("latin-1")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if token and MCPConfig.load(self.settings.data_dir).validate_api_key(token):
            await self.app(scope, receive, send)
            return

        await send({"type": "http.response.start", "status": 401, "headers": [
            (b"content-type", b"application/json"),
            (b"www-authenticate", b'Bearer realm="logpacker"'),
        ]})
        await send({"type": "http.response.body",
                    "body": b'{"error": "Missing or invalid API key. Send Authorization: Bearer <key>."}'})


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    mcp_ctx = _prepare_mcp(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = JobStore(settings)
        if mcp_ctx:
            async with mcp_ctx["session_manager"].run():
                yield
        else:
            yield
        app.state.jobs.close()

    app = FastAPI(title="LogPacker", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)
    app.state.settings = settings

    app.add_middleware(_SessionMiddleware)

    app.include_router(router)
    app.include_router(mcp_router)

    assets = mount_assets(app, STATIC_DIR)
    mcp_mounted = _mount_mcp(app, settings, mcp_ctx)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__, "assets": assets,
                "workers": settings.workers, "mcp": mcp_mounted}

    return app


def _prepare_mcp(settings: Settings) -> dict | None:
    """Create the MCP session manager and ASGI handlers (before the app is built).

    Returns None if the ``mcp`` package is not installed.
    """
    try:
        from mcp.server.mcpserver import MCPServer  # noqa: F401
    except ImportError:
        return None

    from mcp.server.streamable_http_manager import (
        StreamableHTTPASGIApp,
        StreamableHTTPSessionManager,
    )

    from logpacker.mcp.config import MCPConfig
    from logpacker.mcp.server import create_mcp_server

    mcp_config = MCPConfig.load(settings.data_dir)
    base_url = settings.base_url

    # Streamable HTTP transport — build the session manager ourselves
    # so we can enter its run() context in the app lifespan.
    mcp_http = create_mcp_server(settings=settings, mcp_config=mcp_config, base_url=base_url)
    session_manager = StreamableHTTPSessionManager(
        app=mcp_http._lowlevel_server,
    )
    http_handler = StreamableHTTPASGIApp(session_manager)

    # Legacy SSE transport — its lifespan is simpler and works when mounted
    mcp_sse = create_mcp_server(settings=settings, mcp_config=mcp_config, base_url=base_url)
    sse_app = mcp_sse.sse_app(sse_path="/sse", message_path="/messages/")

    return {
        "session_manager": session_manager,
        "http_handler": http_handler,
        "sse_app": sse_app,
        "mcp_config": mcp_config,
    }


def _mount_mcp(app: FastAPI, settings: Settings, mcp_ctx: dict | None) -> bool:
    """Mount MCP endpoints if prepared.

    Two transports are mounted so both Cursor and Claude Desktop work:

    * **Streamable HTTP** at ``/mcp`` — the current MCP standard.
      Claude Desktop and newer clients use this.
      Config: ``"url": "http://host:port/mcp"``

    * **Legacy SSE** at ``/mcp-sse/sse`` — still used by
      some clients (older Cursor versions, custom integrations).
      Config: ``"url": "http://host:port/mcp-sse/sse"``
    """
    if mcp_ctx is None:
        log.info("MCP package not installed — endpoint disabled.")
        return False

    from starlette.routing import Route

    from starlette.applications import Starlette

    # Streamable HTTP at /mcp — use a bare Starlette app with NO lifespan
    # (the session_manager.run() context is entered in the main app lifespan)
    http_app = Starlette(routes=[Route("/", endpoint=mcp_ctx["http_handler"])])
    app.mount("/mcp", _ApiKeyGuard(http_app, settings))

    # Legacy SSE at /mcp-sse
    app.mount("/mcp-sse", _ApiKeyGuard(mcp_ctx["sse_app"], settings))

    n_keys = len(mcp_ctx["mcp_config"].api_keys)
    log.info("MCP endpoints mounted: /mcp (streamable HTTP) + /mcp-sse/sse (legacy SSE)  (%d API key(s))", n_keys)
    return True
