"""ElevationCell model – height/slope/aspect data per grid cell (snail path)."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Float, Integer, DateTime, ForeignKey, UniqueConstraint, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ElevationCell(Base):
    __tablename__ = "elevation_cells"
    __table_args__ = (
        UniqueConstraint("session_id", "snail_path", name="uq_elevation_session_snail"),
        Index("ix_elevation_session", "session_id"),
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

    elevation_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    slope_deg: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    aspect_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    centroid_lat: Mapped[float] = mapped_column(Float, nullable=False)
    centroid_lon: Mapped[float] = mapped_column(Float, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session = relationship("Session", back_populates="elevation_cells")

    def __repr__(self) -> str:
        return f"<ElevationCell {self.snail_path} elev={self.elevation_m}m slope={self.slope_deg}°>"

