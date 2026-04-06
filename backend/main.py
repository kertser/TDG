"""
FastAPI application factory.
"""

from __future__ import annotations

from contextlib import asynccontextmanager

import redis.asyncio as aioredis
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend.config import settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup / shutdown lifecycle events."""
    # ── Startup ───────────────────────────────────────
    app.state.redis = aioredis.from_url(
        settings.REDIS_URL, decode_responses=True
    )

    # Auto-create tables and apply schema updates (dev mode)
    from backend.database import engine, Base
    from sqlalchemy import text
    import backend.models  # noqa: F401 — ensure all models registered
    async with engine.begin() as conn:
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)
        # Add assigned_user_ids column if not exists (migration for existing DBs)
        await conn.execute(text(
            "ALTER TABLE units ADD COLUMN IF NOT EXISTS assigned_user_ids JSONB"
        ))
        # Add name column to sessions (migration for existing DBs)
        await conn.execute(text(
            "ALTER TABLE sessions ADD COLUMN IF NOT EXISTS name VARCHAR(200)"
        ))

    yield
    # ── Shutdown ──────────────────────────────────────
    await app.state.redis.close()


def create_app() -> FastAPI:
    app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        lifespan=lifespan,
    )

    # ── CORS ──────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── API routers (imported lazily to avoid circular imports) ─
    from backend.api import auth, sessions, grid, units, orders, overlays, events, reports, locations
    app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
    app.include_router(sessions.router, prefix="/api/sessions", tags=["sessions"])
    app.include_router(grid.router, prefix="/api/sessions", tags=["grid"])
    app.include_router(units.router, prefix="/api/sessions", tags=["units"])
    app.include_router(orders.router, prefix="/api/sessions", tags=["orders"])
    app.include_router(overlays.router, prefix="/api/sessions", tags=["overlays"])
    app.include_router(events.router, prefix="/api/sessions", tags=["events"])
    app.include_router(reports.router, prefix="/api/sessions", tags=["reports"])
    app.include_router(locations.router, prefix="/api/sessions", tags=["locations"])

    # Scenario endpoints
    from backend.api import scenarios as scenarios_router
    app.include_router(scenarios_router.router, prefix="/api/scenarios", tags=["scenarios"])

    # Admin endpoints
    from backend.api import admin as admin_router
    app.include_router(admin_router.router, prefix="/api/admin", tags=["admin"])

    # WebSocket
    from backend.api import websocket as ws_router
    app.include_router(ws_router.router)

    # ── Serve frontend static files ───────────────────
    import pathlib
    frontend_dir = pathlib.Path(__file__).resolve().parent.parent / "frontend"
    if frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")

    return app


app = create_app()

