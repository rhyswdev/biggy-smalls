"""The API app (docs/decisions/ui-build.md sections 5 and 6).

    uv run uvicorn smalls.api.app:app --host 127.0.0.1 --port 7318      (`make api` sets the environment)

The service is built at start, from the environment unless a test hands `create_app` its own settings, and
closed at shutdown. There is no CORS middleware: the browser never calls this service, the web app's server
does.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from smalls.api import problems, routes
from smalls.api.service import Service
from smalls.api.settings import Settings


def create_app(settings: Settings | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        service = Service(settings or Settings.from_env())
        service.loop = asyncio.get_running_loop()
        app.state.service = service
        try:
            yield
        finally:
            service.close()

    app = FastAPI(title="Smalls API", version="1", lifespan=lifespan, docs_url=None, redoc_url=None)
    problems.install(app)
    app.include_router(routes.router)
    return app


app = create_app()
