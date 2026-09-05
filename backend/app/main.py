"""SKOPE FastAPI application factory and ASGI entrypoint."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .llm import close_client
from .orchestrator import close_executor
from .retrieval import prewarm_models
from .routes import ROUTERS


PROJECT_ROOT = Path(__file__).resolve().parents[2]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"


@asynccontextmanager
async def lifespan(_: FastAPI):
    if get_settings().prewarm_models:
        await asyncio.to_thread(prewarm_models)
    yield
    close_client()
    close_executor()


def create_app() -> FastAPI:
    """Create the API with configuration, middleware, and capability routers."""
    settings = get_settings()
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    for router in ROUTERS:
        application.include_router(router)

    @application.middleware("http")
    async def revalidate_unversioned_frontend(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    application.mount(
        "/static",
        StaticFiles(directory=FRONTEND_ROOT / "static"),
        name="static",
    )

    @application.get("/", include_in_schema=False)
    def frontend() -> FileResponse:
        return FileResponse(FRONTEND_ROOT / "index.html")

    return application


app = create_app()
