"""Scenario model – template for game sessions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import String, Text, Integer, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class Scenario(Base):
    __tablename__ = "scenarios"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Map defaults
    map_center = mapped_column(Geometry("POINT", srid=4326), nullable=True)
    map_zoom: Mapped[int] = mapped_column(Integer, default=12)
    map_bounds = mapped_column(Geometry("POLYGON", srid=4326), nullable=True)

    # Flexible JSONB payloads
    terrain_meta: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    objectives: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    environment: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    grid_settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    initial_units: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    sessions = relationship("Session", back_populates="scenario")

    def __repr__(self) -> str:
        return f"<Scenario {self.title!r}>"

