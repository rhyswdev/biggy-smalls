"""The API's endpoints (docs/decisions/ui-build.md section 6.4). Every body is a pydantic model with length
limits; every error is a problem response."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request

from smalls.api.service import Service

router = APIRouter()


def service(request: Request) -> Service:
    return request.app.state.service


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/capabilities")
def capabilities(request: Request) -> dict[str, Any]:
    return service(request).capabilities()
