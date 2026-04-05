"""
Tick scheduler – background task for automatic tick advancement.

For each running session, acquire a Redis lock, run the tick, release.
This enables auto-play mode where the game advances automatically.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import redis.asyncio as aioredis
from sqlalchemy import select

from backend.config import settings
from backend.database import async_session_factory
from backend.models.session import Session, SessionStatus

logger = logging.getLogger(__name__)


class TickScheduler:
    """Background scheduler that auto-advances running sessions."""

    def __init__(self, redis: aioredis.Redis, interval_seconds: float = 5.0):
        self.redis = redis
        self.interval = interval_seconds
        self._running = False
        self._task: asyncio.Task | None = None

    async def start(self):
        """Start the background tick loop."""
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._loop())
        logger.info("TickScheduler started (interval=%.1fs)", self.interval)

    async def stop(self):
        """Stop the background tick loop."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("TickScheduler stopped")

    async def _loop(self):
        """Main loop: check for running sessions and tick them."""
        while self._running:
            try:
                await self._tick_all_running()
            except Exception:
                logger.exception("TickScheduler error in tick cycle")
            await asyncio.sleep(self.interval)

    async def _tick_all_running(self):
        """Find all running sessions and tick each one (with lock)."""
        async with async_session_factory() as db:
            result = await db.execute(
                select(Session.id).where(Session.status == SessionStatus.running)
            )
            session_ids = [row[0] for row in result.all()]

        for sid in session_ids:
            await self._tick_session(sid)

    async def _tick_session(self, session_id: uuid.UUID):
        """Tick one session, acquiring a Redis lock first."""
        lock_key = f"tick_lock:{session_id}"

        # Try to acquire lock (non-blocking, auto-expires after 30s)
        acquired = await self.redis.set(lock_key, "1", nx=True, ex=30)
        if not acquired:
            return  # Another worker is ticking this session

        try:
            async with async_session_factory() as db:
                from backend.engine.tick import run_tick
                try:
                    result = await run_tick(session_id, db)
                    await db.commit()
                    logger.debug(
                        "Auto-tick session %s: tick=%d events=%d",
                        session_id, result["tick"], result["events_count"]
                    )
                except ValueError as e:
                    logger.warning("Tick skipped for %s: %s", session_id, e)
                except Exception:
                    await db.rollback()
                    logger.exception("Tick failed for session %s", session_id)
        finally:
            await self.redis.delete(lock_key)


# Module-level singleton (initialized during app startup if auto-tick is enabled)
tick_scheduler: TickScheduler | None = None

