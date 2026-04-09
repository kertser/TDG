"""Report model – sitreps, spotreps, contact reports, etc."""

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


class ReportSide(str, enum.Enum):
    blue = "blue"
    red = "red"


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_reports_session_tick", "session_id", "tick"),
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
    channel: Mapped[str] = mapped_column(String(50), nullable=False, default="sitrep")
    from_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("units.id", ondelete="SET NULL"), nullable=True
    )
    to_side: Mapped[ReportSide] = mapped_column(
        SAEnum(ReportSide, name="report_side_enum", create_constraint=True),
        nullable=False,
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    structured_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session = relationship("Session", back_populates="reports")

    def __repr__(self) -> str:
        return f"<Report {self.channel} tick={self.tick}>"

