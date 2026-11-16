"""Local-server hardening.

A server on 127.0.0.1 is still reachable from any web page the user visits (CSRF) and via DNS rebinding.
* Host header must be an allowed host  -> defeats DNS rebinding.
* State-changing requests from browsers must carry a same-origin Origin -> defeats cross-site requests.
* /api/ requests must carry the per-install token (header or SameSite=Strict cookie) -> see core/token.py.
"""

from __future__ import annotations

import hmac
from collections.abc import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

from clipper.api.errors import problem
from clipper.core import token as tokens

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class LocalGuardMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, allowed_hosts: list[str], token: str) -> None:
        super().__init__(app)
        self.allowed = {h.lower() for h in allowed_hosts}
        self.token = token.encode()

    def _authorised(self, request: Request) -> bool:
        given = request.headers.get(tokens.HEADER) or request.cookies.get(tokens.COOKIE) or ""
        return hmac.compare_digest(given.encode(), self.token)

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        host = (request.headers.get("host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host not in self.allowed and "*" not in self.allowed:
            return problem(
                400,
                "HOST_NOT_ALLOWED",
                "This host name is not allowed.",
                "Open the app via http://127.0.0.1 or add the host to CLIPPER_ALLOWED_HOSTS.",
            )
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None:
                o = urlsplit(origin)
                if (o.hostname or "").lower() not in self.allowed and "*" not in self.allowed:
                    return problem(403, "CROSS_ORIGIN_BLOCKED", "Cross-site request blocked.")
        if request.url.path.startswith("/api/") and not self._authorised(request):
            return problem(
                401,
                "TOKEN_REQUIRED",
                "Missing or wrong access token.",
                "Open Clipper at http://127.0.0.1:8765 (the page grants access), or send the X-Clipper-Token "
                "header with the contents of CLIPPER_DATA_DIR/api_token.",
            )
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "no-referrer")
        return resp
