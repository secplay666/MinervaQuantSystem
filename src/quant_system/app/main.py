"""FastAPI application factory (ADR-010).

    app = create_app(load_settings(root))

API under /api/v1 (OpenAPI at /api/openapi.json, docs at /api/docs); the
built PC frontend, when configured, is served at / with an SPA fallback.
The server binds to 127.0.0.1 only; Caddy terminates TLS in front of it.
"""

from __future__ import annotations

from pathlib import Path

import duckdb
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import (accounts, auth, decisions, etf, events, invitations, market, money_map, pm, pm_board, pm_detail,
                  system, users)
from .db.base import open_database
from .etf import EtfQueries
from .market import MarketQueries
from .money_map import money_map_for
from .position_jobs import StageBacktests
from .rbac import sync_roles
from .settings import AppSettings

API_PREFIX = "/api/v1"
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
}


def create_app(settings: AppSettings) -> FastAPI:
    engine, sessions = open_database(settings.db_path)
    with sessions() as session:
        sync_roles(session)
        session.commit()
    app = FastAPI(title="Minerva 决策辅助 API", version="1.0", docs_url="/api/docs", redoc_url=None,
                  openapi_url="/api/openapi.json")
    app.state.settings = settings
    app.state.engine = engine
    app.state.sessions = sessions
    app.state.market = MarketQueries(settings.market_db, settings.root / "configs" / "market_rules" / "cn_a_share.json")
    app.state.etf = EtfQueries(settings.market_db, settings.root / "configs" / "etf" / "broad_groups.json")
    app.state.money_map = money_map_for(settings.market_db)
    app.state.stage_jobs = StageBacktests(app.state.market)
    app.state.jobs = {}
    app.add_middleware(GZipMiddleware, minimum_size=1024)
    if settings.cors_origins:
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                           allow_methods=["*"], allow_headers=["*"])

    @app.middleware("http")
    async def headers(request: Request, call_next):  # type: ignore[no-untyped-def]
        response = await call_next(request)
        for key, value in SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        if request.url.path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        if request.url.scheme == "https":
            response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
        response.headers["X-Environment"] = settings.environment
        return response

    @app.exception_handler(ValueError)
    async def value_error(_request: Request, exc: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": {"code": "invalid", "message": str(exc)}})

    @app.exception_handler(duckdb.CatalogException)
    async def incomplete_market(_request: Request, exc: duckdb.CatalogException) -> JSONResponse:
        # A catalog built without some dataset (a partial rebuild, a fresh install): say so instead of a 500.
        return JSONResponse(status_code=503, content={"detail": {"code": "incomplete_market_data",
                                                                 "message": f"行情库缺少所需的数据：{exc}"}})

    for module in (system, auth, users, invitations, events, market, etf, money_map, accounts, decisions, pm,
                   pm_detail, pm_board):
        app.include_router(module.router, prefix=API_PREFIX)
    if settings.mobile_dir is not None and (settings.mobile_dir / "index.html").is_file():
        # Mounted before the PC catch-all; relative asset paths and hash routes need nothing else.
        app.mount("/m", StaticFiles(directory=settings.mobile_dir, html=True), name="mobile")
    if settings.web_dir is not None and (settings.web_dir / "index.html").is_file():
        _mount_frontend(app, settings.web_dir)
    return app


def _mount_frontend(app: FastAPI, web_dir: Path) -> None:
    assets = web_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    index = web_dir / "index.html"

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):  # type: ignore[no-untyped-def]  # client-side routes fall back to index.html
        if path.startswith("api/"):
            return JSONResponse(status_code=404, content={"detail": {"code": "not_found", "message": "接口不存在"}})
        candidate = (web_dir / path).resolve()
        if path and candidate.is_file() and web_dir.resolve() in candidate.parents:
            return FileResponse(candidate)
        return FileResponse(index)
