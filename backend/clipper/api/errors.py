"""RFC 9457 problem+json errors with stable machine codes and user-facing hints."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from clipper.core.logging import log


class ApiError(Exception):
    def __init__(self, status: int, code: str, title: str, hint: str | None = None, **extra: Any) -> None:
        super().__init__(title)
        self.status, self.code, self.title, self.hint, self.extra = status, code, title, hint, extra


def problem(status: int, code: str, title: str, hint: str | None = None, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://clipper.dev/errors/{code.lower()}",
        "title": title,
        "status": status,
        "code": code,
    }
    if hint:
        body["hint"] = hint
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type="application/problem+json")


def install(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api(_: Request, e: ApiError) -> JSONResponse:
        return problem(e.status, e.code, e.title, e.hint, **e.extra)

    @app.exception_handler(RequestValidationError)
    async def _val(_: Request, e: RequestValidationError) -> JSONResponse:
        errs = [{"loc": list(x["loc"]), "msg": x["msg"]} for x in e.errors()]
        return problem(
            422, "VALIDATION_ERROR", "Some fields are invalid.", "Check the highlighted fields.", errors=errs
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, e: StarletteHTTPException) -> JSONResponse:
        code = {404: "NOT_FOUND", 405: "METHOD_NOT_ALLOWED"}.get(e.status_code, "HTTP_ERROR")
        return problem(e.status_code, code, str(e.detail))

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, e: Exception) -> JSONResponse:
        log.error("api.unhandled", path=request.url.path, exc_info=e)
        return problem(
            500,
            "INTERNAL_ERROR",
            "Something went wrong on our side.",
            "Try again. If it keeps happening, check the server log and report a bug.",
        )
