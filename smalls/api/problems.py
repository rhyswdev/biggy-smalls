"""Errors as `application/problem+json` (RFC 9457): a type, a title, the status, a detail a person can read,
and the path. An unexpected error says only that it happened: its message may hold anything."""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

MEDIA = "application/problem+json"
log = logging.getLogger("smalls.api")


class Problem(Exception):
    """An error the API means to return: a status, a short title and a detail."""

    def __init__(self, status: int, title: str, detail: str = ""):
        super().__init__(detail or title)
        self.status, self.title, self.detail = status, title, detail


def problem(status: int, title: str, detail: str, request: Request, **extra) -> JSONResponse:
    body = {"type": "about:blank", "title": title, "status": status, "detail": detail, "instance": request.url.path, **extra}
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def install(app: FastAPI) -> None:
    @app.exception_handler(Problem)
    async def _problem(request: Request, exc: Problem):
        return problem(exc.status, exc.title, exc.detail, request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return problem(exc.status_code, "Not found" if exc.status_code == 404 else "Request failed", str(exc.detail), request)

    @app.exception_handler(RequestValidationError)
    async def _invalid(request: Request, exc: RequestValidationError):
        errors = [{"loc": list(e.get("loc", [])), "msg": e.get("msg", "")} for e in exc.errors()]
        return problem(422, "Invalid request", "The request did not validate.", request, errors=errors)

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception):
        log.exception("unexpected error on %s", request.url.path)
        return problem(500, "Internal error", "The API could not complete the request.", request)
