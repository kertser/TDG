"""
Application configuration via environment variables.
Uses pydantic-settings for typed, validated config with .env file support.
"""

from __future__ import annotations

import json
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent.parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────
    DATABASE_URL: str = "postgresql+asyncpg://tdg:tdg_secret@localhost:5432/tdg"
    DATABASE_URL_SYNC: str = "postgresql://tdg:tdg_secret@localhost:5432/tdg"

    # ── Redis ─────────────────────────────────────────
    REDIS_URL: str = "redis://localhost:6379/0"

    # ── Auth / Security ──────────────────────────────
    SECRET_KEY: str = "change-me-to-a-random-secret-string-in-production"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    ALGORITHM: str = "HS256"
    ADMIN_PASSWORD: str = "admin"  # password to unlock admin panel

    # ── OpenAI ────────────────────────────────────────
    OPENAI_API_KEY: str = ""
    OPENAI_MODEL: str = "gpt-4.1"

    # ── Application ───────────────────────────────────
    APP_NAME: str = "TDG Tactical Decision Game Platform"
    APP_VERSION: str = "0.2.0"
    DEBUG: bool = True
    CORS_ORIGINS: str = '["http://localhost:8000","http://localhost:3000","http://127.0.0.1:8000"]'

    @property
    def cors_origins_list(self) -> list[str]:
        return json.loads(self.CORS_ORIGINS)

    # ── Simulation defaults ───────────────────────────
    DEFAULT_TICK_INTERVAL_SEC: int = 60  # 1 minute of game time per tick


settings = Settings()

