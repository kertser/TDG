"""Event model – append-only game event log."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Text, Integer, DateTime, ForeignKey,
    Enum as SAEnum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class EventVisibility(str, enum.Enum):
    all = "all"
    blue = "blue"
    red = "red"
    admin = "admin"


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        Index("ix_events_session_tick", "session_id", "tick"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    tick: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    game_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    event_type: Mapped[str] = mapped_column(String(50), nullable=False)
    visibility: Mapped[EventVisibility] = mapped_column(
        SAEnum(EventVisibility, name="event_visibility_enum", create_constraint=True),
        default=EventVisibility.all,
    )
    actor_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id"), nullable=True
    )
    target_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id"), nullable=True
    )
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    text_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session = relationship("Session", back_populates="events")

    def __repr__(self) -> str:
        return f"<Event {self.event_type} tick={self.tick}>"

