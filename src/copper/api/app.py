"""FastAPI application factory."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from copper.api.routes import minds, workflows

_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def create_app() -> FastAPI:
    app = FastAPI(
        title="Copper",
        description="Mentecobres — AI-maintained knowledge bases",
        version="0.1.0",
        docs_url="/api/docs",
        redoc_url="/api/redoc",
    )

    # Routers
    app.include_router(minds.router)
    app.include_router(workflows.router)

    # Static files and templates (only if directories exist)
    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR))

    @app.get("/", include_in_schema=False)
    async def ui(request: Request):
        from copper.core.coppermind import CopperMind

        minds_list = CopperMind.list_all()
        # Starlette 1.0 API: request and context are separate args
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"minds": minds_list},
        )

    return app
