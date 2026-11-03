"""Readable JSON errors: every 4xx/5xx body is `{"error": <code>, "detail": <sentence>}`.

`detail` is a sentence a project manager can act on (the web app shows it as-is);
`error` is a short machine code. Raise `ApiError` from routes; the handlers below turn
FastAPI validation errors, Starlette HTTP errors and unexpected exceptions into the same shape.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("punchwalk.api")

ERROR_CODES: dict[int, str] = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    410: "gone",
    413: "too_large",
    415: "unsupported_media_type",
    416: "range_not_satisfiable",
    422: "invalid_request",
    429: "rate_limited",
    500: "server_error",
    501: "not_implemented",
    503: "unavailable",
}

DEFAULT_DETAILS: dict[int, str] = {
    404: "Nothing here. Check the link and try again.",
    405: "This address doesn't accept that kind of request.",
    500: "Something went wrong on our side. Try again in a moment.",
}


class ApiError(StarletteHTTPException):
    """An error with a readable `detail` and an optional explicit `error` code."""

    def __init__(
        self,
        status_code: int,
        detail: str,
        *,
        error: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.error = error or ERROR_CODES.get(status_code, "error")


def not_found(what: str) -> ApiError:
    return ApiError(404, f"{what} wasn't found. It may have been removed.")


def error_body(status_code: int, detail: Any, error: str | None = None) -> dict[str, str]:
    if not isinstance(detail, str) or not detail.strip():
        detail = DEFAULT_DETAILS.get(status_code, "The request couldn't be completed.")
    return {"error": error or ERROR_CODES.get(status_code, "error"), "detail": detail}


def error_response(status_code: int, detail: Any, error: str | None = None, headers=None) -> JSONResponse:
    return JSONResponse(error_body(status_code, detail, error), status_code=status_code, headers=headers)


def _validation_detail(exc: RequestValidationError) -> str:
    parts = []
    for err in exc.errors()[:5]:
        loc = [str(p) for p in err.get("loc", ()) if p not in ("body", "query", "path")]
        where = ".".join(loc)
        msg = str(err.get("msg", "is invalid"))
        parts.append(f"{where}: {msg}" if where else msg)
    return "Some fields aren't right: " + "; ".join(parts) + "." if parts else "The request isn't valid."


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = getattr(exc, "error", None)
        detail = exc.detail
        if detail in (None, "") or (isinstance(detail, str) and detail in ("Not Found", "Method Not Allowed")):
            detail = DEFAULT_DETAILS.get(exc.status_code)
        return error_response(exc.status_code, detail, code, headers=getattr(exc, "headers", None))

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(422, _validation_detail(exc))

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return error_response(500, DEFAULT_DETAILS[500])
