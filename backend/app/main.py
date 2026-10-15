"""FastAPI application entry point."""
from __future__ import annotations

import logging
import logging.handlers
from contextlib import asynccontextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import models  # noqa: F401
from .api.routes import router
from .config import ROOT_DIR, get_env
from .db import create_all, get_engine, init_engine
from .llama_proc import llama
from .state import orchestrator

log = logging.getLogger("forge")


def setup_logging() -> None:
    env = get_env()
    handler = logging.handlers.RotatingFileHandler(env.logs_dir / "forge.log", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    root.setLevel(logging.INFO)


def migrate() -> None:
    cfg = Config(str(ROOT_DIR / "backend" / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT_DIR / "backend" / "alembic"))
    cfg.set_main_option("sqlalchemy.url", get_env().db_url)
    try:
        command.upgrade(cfg, "head")
    except Exception:
        log.exception("alembic upgrade failed; falling back to create_all")
        create_all()


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    init_engine()
    migrate()
    n = orchestrator.recover_interrupted()
    if n:
        log.info("recovered %d interrupted task(s) -> BLOCKED (resumable)", n)
    yield
    log.info("shutting down")
    for pid in list(orchestrator.runs):
        if orchestrator.is_running(pid):
            await orchestrator.cancel(pid)
    await orchestrator.services.shutdown()
    llama.stop()
    get_engine().dispose()


def create_app() -> FastAPI:
    env = get_env()
    app = FastAPI(title="ForgeAgent", version="0.1.0", lifespan=lifespan)
    app.add_middleware(CORSMiddleware, allow_origins=env.allowed_origins, allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

    @app.middleware("http")
    async def token_guard(request: Request, call_next):
        if env.api_token and request.url.path.startswith("/api") and request.headers.get("x-forge-token") != env.api_token \
                and request.method != "OPTIONS" and not request.url.path.startswith("/api/ws"):
            return JSONResponse({"detail": "invalid token"}, status_code=401)
        return await call_next(request)

    app.include_router(router)

    dist = ROOT_DIR / "frontend" / "dist"
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            f = (dist / full_path).resolve()
            if full_path and f.is_file() and dist.resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(dist / "index.html")

    return app


app = create_app()
