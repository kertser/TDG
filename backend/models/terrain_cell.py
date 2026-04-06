"""TerrainCell model – terrain type classification per grid cell (snail path)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Float, Integer, DateTime, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class TerrainCell(Base):
    __tablename__ = "terrain_cells"
    __table_args__ = (
        UniqueConstraint("session_id", "snail_path", name="uq_terrain_session_snail"),
        Index("ix_terrain_session", "session_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    snail_path: Mapped[str] = mapped_column(String(50), nullable=False)
    depth: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    terrain_type: Mapped[str] = mapped_column(String(20), nullable=False, default="open")
    modifiers: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="default")
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)

    elevation_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    slope_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    raw_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session = relationship("Session", back_populates="terrain_cells")

    def __repr__(self) -> str:
        return f"<TerrainCell {self.snail_path} type={self.terrain_type} src={self.source}>"

