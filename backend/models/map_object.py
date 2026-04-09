"""MapObject model – tactical obstacles, fortifications, and static structures on the battlefield."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import String, Float, Boolean, DateTime, ForeignKey, Enum as SAEnum, Index
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ObjectCategory(str, enum.Enum):
    obstacle = "obstacle"
    structure = "structure"
    effect = "effect"


class ObjectSide(str, enum.Enum):
    blue = "blue"
    red = "red"
    neutral = "neutral"


class MapObject(Base):
    __tablename__ = "map_objects"
    __table_args__ = (
        Index("ix_map_objects_session", "session_id"),
        Index("ix_map_objects_session_category", "session_id", "object_category"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[ObjectSide] = mapped_column(
        SAEnum(ObjectSide, name="object_side_enum", create_constraint=True),
        default=ObjectSide.neutral,
    )
    object_type: Mapped[str] = mapped_column(String(50), nullable=False)
    object_category: Mapped[ObjectCategory] = mapped_column(
        SAEnum(ObjectCategory, name="object_category_enum", create_constraint=True),
        nullable=False,
    )
    geometry = mapped_column(
        Geometry("GEOMETRY", srid=4326), nullable=True
    )
    properties: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    style_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    label: Mapped[str | None] = mapped_column(String(200), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    health: Mapped[float] = mapped_column(Float, default=1.0)  # 0.0–1.0

    # Discovery / fog-of-war for map objects.
    # Obstacles default to hidden (False); structures default to revealed (True).
    # Once a side's unit has LOS to the object, it becomes discovered for that side permanently.
    discovered_by_blue: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    discovered_by_red: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")

    placed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    session = relationship("Session", back_populates="map_objects")

    def __repr__(self) -> str:
        return f"<MapObject {self.object_type} [{self.side.value}] active={self.is_active}>"

