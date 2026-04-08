"""Apply the discovered_by_blue/red migration directly via async SQLAlchemy."""
import asyncio
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from backend.config import settings


async def run():
    engine = create_async_engine(settings.DATABASE_URL)
    async with engine.begin() as conn:
        await conn.execute(text(
            "ALTER TABLE map_objects ADD COLUMN IF NOT EXISTS discovered_by_blue BOOLEAN NOT NULL DEFAULT false"
        ))
        await conn.execute(text(
            "ALTER TABLE map_objects ADD COLUMN IF NOT EXISTS discovered_by_red BOOLEAN NOT NULL DEFAULT false"
        ))
        await conn.execute(text(
            "UPDATE map_objects SET discovered_by_blue = true, discovered_by_red = true WHERE object_category = 'structure'"
        ))
    await engine.dispose()
    print("Migration applied successfully!")


if __name__ == "__main__":
    asyncio.run(run())

