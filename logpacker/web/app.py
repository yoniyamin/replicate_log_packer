"""FastAPI application factory."""

from __future__ import annotations

import secrets
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request

from logpacker import __version__
from logpacker.config import Settings, get_settings
from logpacker.web.assets import mount_assets
from logpacker.web.jobs import JobStore
from logpacker.web.routes import router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
SESSION_COOKIE = "lp_session"
SESSION_MAX_AGE = 30 * 24 * 3600


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.jobs = JobStore(settings)
        yield
        app.state.jobs.close()

    app = FastAPI(title="LogPacker", version=__version__, docs_url="/api/docs", redoc_url=None, lifespan=lifespan)

    @app.middleware("http")
    async def session_cookie(request: Request, call_next):
        """Each browser gets its own session id, which scopes the jobs it can see."""
        session = request.cookies.get(SESSION_COOKIE)
        fresh = not session or len(session) != 32
        request.state.session = secrets.token_hex(16) if fresh else session
        response = await call_next(request)
        if fresh:
            response.set_cookie(SESSION_COOKIE, request.state.session, max_age=SESSION_MAX_AGE,
                                httponly=True, samesite="strict")
        return response

    app.include_router(router)

    assets = mount_assets(app, STATIC_DIR)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__, "assets": assets, "workers": settings.workers}

    return app
