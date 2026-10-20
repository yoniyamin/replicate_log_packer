"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from logpacker import __version__
from logpacker.config import Settings, get_settings
from logpacker.web.jobs import JobStore
from logpacker.web.routes import router

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title="LogPacker", version=__version__, docs_url="/api/docs", redoc_url=None)
    app.state.jobs = JobStore(settings)
    app.include_router(router)

    @app.get("/healthz")
    def healthz() -> dict:
        return {"ok": True, "version": __version__}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app
