"""Serves the built web UI (frontend/ -> `npm run build` -> backend/clipper/web/dist) from `clipper serve`.

Every HTML page response sets the access-token cookie (HttpOnly, SameSite=Strict) and a strict Content
Security Policy. Unknown non-API paths return index.html so client-side routes survive a reload.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from clipper.api.errors import ApiError
from clipper.core import token as tokens

DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data: blob:; "
    "media-src 'self' blob:; font-src 'self' data:; connect-src 'self'; object-src 'none'; "
    "base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
)
NOT_BUILT = """<!doctype html><html lang="en"><meta charset="utf-8"><title>Clipper</title>
<body>
<h1>Clipper API is running</h1>
<p>The web interface has not been built. From the repository run:</p>
<pre>cd frontend
npm ci
npm run build</pre>
<p>then reload this page. The API documentation is at <a href="/api/docs">/api/docs</a>.</p>
</body></html>"""


def install(app: FastAPI, token: str, dist: Path = DIST) -> None:
    # Vite's assetsDir is "static" (frontend/vite.config.ts); "/assets/..." belongs to the app's own routes
    if (dist / "static").is_dir():
        app.mount("/static", StaticFiles(directory=dist / "static"), name="web-static")

    def page() -> Response:
        index = dist / "index.html"
        resp: Response = (
            FileResponse(index, media_type="text/html") if index.exists() else HTMLResponse(NOT_BUILT)
        )
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["Cache-Control"] = "no-store"
        resp.set_cookie(tokens.COOKIE, token, httponly=True, samesite="strict", path="/")
        return resp

    @app.get("/", include_in_schema=False)
    def root() -> Response:
        return page()

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str, request: Request) -> Response:
        if path.startswith("api/") or path == "api":
            raise ApiError(404, "NOT_FOUND", "Not found.")
        # top-level files from the build (favicon etc.); never anything outside dist
        if "/" not in path and path not in ("", ".", ".."):
            f = (dist / path).resolve()
            if f.parent == dist.resolve() and f.is_file() and f.name != "index.html":
                return FileResponse(f)
        return page()
