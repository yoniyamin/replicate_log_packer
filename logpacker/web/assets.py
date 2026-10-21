"""Versioned static assets: /assets/<hash>/... changes whenever any static file changes,
so browsers never run stale JS modules after an upgrade, and can cache assets forever."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from starlette.types import Scope


class ImmutableStatic(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


def asset_version(static_dir: Path) -> str:
    digest = hashlib.sha1()
    for path in sorted(static_dir.rglob("*")):
        if path.is_file():
            stat = path.stat()
            digest.update(f"{path.relative_to(static_dir)}:{stat.st_size}:{stat.st_mtime_ns}".encode())
    return digest.hexdigest()[:10]


def mount_assets(app: FastAPI, static_dir: Path) -> str:
    version = asset_version(static_dir)
    prefix = f"/assets/{version}/"
    index = (static_dir / "index.html").read_text(encoding="utf-8")
    index = index.replace('href="css/', f'href="{prefix}css/').replace('src="js/', f'src="{prefix}js/')

    @app.get("/", include_in_schema=False)
    def page() -> HTMLResponse:
        return HTMLResponse(index, headers={"Cache-Control": "no-cache"})

    app.mount(prefix.rstrip("/"), ImmutableStatic(directory=static_dir), name="assets")
    return version
