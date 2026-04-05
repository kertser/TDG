"""PlanningOverlay model – collaborative drawing layer."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import String, DateTime, ForeignKey, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class OverlayType(str, enum.Enum):
    arrow = "arrow"
    polyline = "polyline"
    polygon = "polygon"
    marker = "marker"
    label = "label"
    rectangle = "rectangle"
    circle = "circle"


class OverlaySide(str, enum.Enum):
    blue = "blue"
    red = "red"
    observer = "observer"


class PlanningOverlay(Base):
    __tablename__ = "planning_overlays"
    __table_args__ = (
        Index("ix_overlays_session_side", "session_id", "side"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    author_user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    side: Mapped[OverlaySide] = mapped_column(
        SAEnum(OverlaySide, name="overlay_side_enum", create_constraint=True),
        nullable=False,
    )
    overlay_type: Mapped[OverlayType] = mapped_column(
        SAEnum(OverlayType, name="overlay_type_enum", create_constraint=True),
        nullable=False,
    )
    geometry = mapped_column(
        Geometry("GEOMETRY", srid=4326), nullable=True
    )
    style_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    session = relationship("Session", back_populates="overlays")
    author = relationship("User", back_populates="overlays")

    def __repr__(self) -> str:
        return f"<Overlay {self.overlay_type.value} by={self.author_user_id}>"

