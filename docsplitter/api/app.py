"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from docsplitter.api.routers import channels, review, status, upload
from docsplitter.api.schemas import HealthResponse
from docsplitter.channelstore import ChannelStore
from docsplitter.config import AppConfig, get_config
from docsplitter.db import create_tables, init_db
from docsplitter.pipeline import Pipeline

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent.parent / "static"


def create_app(cfg: AppConfig | None = None) -> FastAPI:
    if cfg is None:
        cfg = get_config()

    init_db(cfg.database.url)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
        await create_tables()

        channel_store = ChannelStore()
        seeded = await channel_store.seed_from_yaml(cfg.channels)
        if seeded:
            logger.info("Seeded %d channels from YAML to database", seeded)

        app.state.channel_store = channel_store
        app.state.pipeline = Pipeline(ai_cfg=cfg.ai, output_cfg=cfg.output)
        logger.info("DocSplitter API started. Config: %s", cfg.redacted())
        yield
        logger.info("DocSplitter API shutting down")

    app = FastAPI(
        title="DocSplitter",
        description="Intelligent multi-document PDF splitter",
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    prefix = "/api/v1"
    app.include_router(upload.router, prefix=prefix)
    app.include_router(status.router, prefix=prefix)
    app.include_router(review.router, prefix=prefix)
    app.include_router(channels.router, prefix=prefix)

    # Serve static assets (CSS, JS if added later)
    if STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/admin", include_in_schema=False)
    async def admin_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/help", include_in_schema=False)
    async def help_ui() -> FileResponse:
        return FileResponse(STATIC_DIR / "help.html")

    @app.get("/", include_in_schema=False)
    async def root() -> FileResponse:
        return FileResponse(STATIC_DIR / "admin.html")

    @app.get("/api/v1/health", response_model=HealthResponse, tags=["admin"])
    async def health() -> HealthResponse:
        ai_ok = await _check_ai_reachable(cfg)
        return HealthResponse(
            status="ok" if ai_ok else "degraded",
            ai_reachable=ai_ok,
            model=cfg.ai.model,
            database=cfg.database.url.split("///")[-1],
        )

    @app.get("/api/v1/config", tags=["admin"])
    async def config_view() -> dict:
        return cfg.redacted()

    return app


async def _check_ai_reachable(cfg: AppConfig) -> bool:
    try:
        import httpx
        async with httpx.AsyncClient(timeout=5) as client:
            if cfg.ai.api_version:
                # Azure OpenAI
                url = (
                    cfg.ai.base_url.rstrip("/")
                    + f"/openai/models?api-version={cfg.ai.api_version}"
                )
                headers = {"api-key": cfg.ai.api_key}
            else:
                # Standard OpenAI-compatible
                url = cfg.ai.base_url.rstrip("/") + "/models"
                headers = {}
                if cfg.ai.api_key:
                    headers["Authorization"] = f"Bearer {cfg.ai.api_key}"
            r = await client.get(url, headers=headers)
            return r.status_code < 500
    except Exception:
        return False
