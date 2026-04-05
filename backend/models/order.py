"""Order and LocationReference models."""

from __future__ import annotations

import enum
import uuid
from datetime import datetime, timezone

from geoalchemy2 import Geometry
from sqlalchemy import (
    String, Text, Float, Integer, Boolean, DateTime, ForeignKey,
    Enum as SAEnum, Index,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class OrderStatus(str, enum.Enum):
    pending = "pending"
    validated = "validated"
    executing = "executing"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class OrderSide(str, enum.Enum):
    blue = "blue"
    red = "red"


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("ix_orders_session_status", "session_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    issued_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    issued_by_side: Mapped[OrderSide] = mapped_column(
        SAEnum(OrderSide, name="order_side_enum", create_constraint=True),
        nullable=False,
    )
    target_unit_ids = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    order_type: Mapped[str] = mapped_column(String(50), nullable=True)
    original_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_order: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    parsed_intent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[OrderStatus] = mapped_column(
        SAEnum(OrderStatus, name="order_status_enum", create_constraint=True),
        default=OrderStatus.pending,
    )
    issued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    validated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Relationships
    session = relationship("Session", back_populates="orders")
    location_references = relationship(
        "LocationReference", back_populates="order", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Order {self.id} type={self.order_type} status={self.status.value}>"


# ── LocationReference ─────────────────────────────────


class ReferenceType(str, enum.Enum):
    coordinate = "coordinate"
    grid = "grid"
    square = "square"
    snail = "snail"
    terrain = "terrain"
    mixed = "mixed"


class LocationReference(Base):
    __tablename__ = "location_references"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("orders.id", ondelete="CASCADE"), nullable=True
    )
    source_text: Mapped[str] = mapped_column(String(200), nullable=False)
    reference_type: Mapped[ReferenceType] = mapped_column(
        SAEnum(ReferenceType, name="reference_type_enum", create_constraint=True),
        nullable=False,
    )
    normalized_ref: Mapped[str] = mapped_column(String(100), nullable=False)
    resolved_geometry = mapped_column(
        Geometry("GEOMETRY", srid=4326), nullable=True
    )
    resolution_depth: Mapped[int | None] = mapped_column(Integer, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    validated: Mapped[bool] = mapped_column(Boolean, default=False)

    # Relationships
    order = relationship("Order", back_populates="location_references")

    def __repr__(self) -> str:
        return f"<LocationRef {self.normalized_ref!r} type={self.reference_type.value}>"

