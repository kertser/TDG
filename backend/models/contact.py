"""Contact model – detected enemy unit sightings."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import (
    String, Float, Integer, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class ContactSide(str, enum.Enum):
    blue = "blue"
    red = "red"


class Contact(Base):
    __tablename__ = "contacts"
    __table_args__ = (
        Index("ix_contacts_session_side", "session_id", "observing_side"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    observing_side: Mapped[ContactSide] = mapped_column(
        SAEnum(ContactSide, name="contact_side_enum", create_constraint=True),
        nullable=False,
    )
    observing_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id", ondelete="SET NULL"), nullable=True
    )
    # Internal tracking: which actual enemy unit this contact refers to.
    # Not exposed to players (they only see estimated_type/location).
    target_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    estimated_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    estimated_size: Mapped[str | None] = mapped_column(String(50), nullable=True)

    location_estimate = mapped_column(
        Geometry("POINT", srid=4326), nullable=True
    )
    location_accuracy_m: Mapped[float] = mapped_column(Float, default=500.0)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)

    last_seen_tick: Mapped[int] = mapped_column(Integer, default=0)
    last_seen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    source: Mapped[str] = mapped_column(String(50), default="visual")
    is_stale: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    session = relationship("Session", back_populates="contacts")

    def __repr__(self) -> str:
        return f"<Contact {self.id} side={self.observing_side.value} stale={self.is_stale}>"

