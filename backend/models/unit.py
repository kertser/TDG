"""Unit model – represents a military unit on the battlefield."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import (
    String, Float, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class CommsStatus(str, enum.Enum):
    operational = "operational"
    degraded = "degraded"
    offline = "offline"


class UnitSide(str, enum.Enum):
    blue = "blue"
    red = "red"


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (
        Index("ix_units_session_side", "session_id", "side"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    side: Mapped[UnitSide] = mapped_column(
        SAEnum(UnitSide, name="unit_side_enum", create_constraint=True),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    unit_type: Mapped[str] = mapped_column(String(50), nullable=False)
    sidc: Mapped[str] = mapped_column(String(30), nullable=False, default="")
    parent_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id"), nullable=True
    )

    # Position & orientation
    position = mapped_column(
        Geometry("POINT", srid=4326), nullable=True
    )
    heading_deg: Mapped[float] = mapped_column(Float, default=0.0)

    # Combat state (all 0.0–1.0)
    strength: Mapped[float] = mapped_column(Float, default=1.0)
    ammo: Mapped[float] = mapped_column(Float, default=1.0)
    morale: Mapped[float] = mapped_column(Float, default=1.0)
    suppression: Mapped[float] = mapped_column(Float, default=0.0)

    # Communications
    comms_status: Mapped[CommsStatus] = mapped_column(
        SAEnum(CommsStatus, name="comms_status_enum", create_constraint=True),
        default=CommsStatus.operational,
    )

    # Task & capabilities (flexible JSONB)
    current_task: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    capabilities: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Movement & detection base stats
    move_speed_mps: Mapped[float] = mapped_column(Float, default=5.0)      # ~18 km/h
    detection_range_m: Mapped[float] = mapped_column(Float, default=2000.0) # 2 km

    # User assignment — list of user_id strings who can command this unit
    assigned_user_ids: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    is_destroyed: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    session = relationship("Session", back_populates="units")
    parent_unit = relationship("Unit", remote_side="Unit.id", backref="sub_units")

    def __repr__(self) -> str:
        return f"<Unit {self.name!r} [{self.side.value}]>"

