"""GridDefinition model – tactical grid configuration per session."""

from __future__ import annotations

import uuid

from geoalchemy2 import Geometry
from sqlalchemy import String, Float, Integer, ForeignKey
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class GridDefinition(Base):
    __tablename__ = "grid_definitions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False, unique=True,
    )

    # Grid origin – SW corner
    origin = mapped_column(
        Geometry("POINT", srid=4326), nullable=False
    )
    orientation_deg: Mapped[float] = mapped_column(Float, default=0.0)
    base_square_size_m: Mapped[float] = mapped_column(Float, nullable=False)

    columns: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[int] = mapped_column(Integer, nullable=False)

    labeling_scheme: Mapped[str] = mapped_column(
        String(20), default="alphanumeric"
    )
    recursion_base: Mapped[int] = mapped_column(Integer, default=3)
    max_depth: Mapped[int] = mapped_column(Integer, default=3)
    settings_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    session = relationship("Session", back_populates="grid_definition")

    def __repr__(self) -> str:
        return f"<Grid {self.columns}x{self.rows} square={self.base_square_size_m}m>"

