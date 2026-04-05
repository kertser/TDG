"""Session (game) and SessionParticipant models."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    String, Integer, DateTime, ForeignKey, Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

# ── Enums ─────────────────────────────────────────────

import enum


class SessionStatus(str, enum.Enum):
    lobby = "lobby"
    running = "running"
    paused = "paused"
    finished = "finished"


class Side(str, enum.Enum):
    blue = "blue"
    red = "red"
    observer = "observer"
    admin = "admin"


# ── Session ───────────────────────────────────────────


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    scenario_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("scenarios.id"), nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status", create_constraint=True),
        default=SessionStatus.lobby,
    )
    current_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tick: Mapped[int] = mapped_column(Integer, default=0)
    tick_interval: Mapped[int] = mapped_column(Integer, default=60)  # seconds
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    settings: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    scenario = relationship("Scenario", back_populates="sessions")
    participants = relationship("SessionParticipant", back_populates="session", cascade="all, delete-orphan")
    grid_definition = relationship("GridDefinition", back_populates="session", uselist=False, cascade="all, delete-orphan")
    units = relationship("Unit", back_populates="session", cascade="all, delete-orphan")
    orders = relationship("Order", back_populates="session", cascade="all, delete-orphan")
    overlays = relationship("PlanningOverlay", back_populates="session", cascade="all, delete-orphan")
    contacts = relationship("Contact", back_populates="session", cascade="all, delete-orphan")
    events = relationship("Event", back_populates="session", cascade="all, delete-orphan")
    reports = relationship("Report", back_populates="session", cascade="all, delete-orphan")
    red_agents = relationship("RedAgent", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self) -> str:
        return f"<Session {self.id} status={self.status.value}>"


# ── SessionParticipant ────────────────────────────────


class SessionParticipant(Base):
    __tablename__ = "session_participants"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    side: Mapped[Side] = mapped_column(
        SAEnum(Side, name="side_enum", create_constraint=True),
        default=Side.blue,
    )
    role: Mapped[str] = mapped_column(String(50), default="commander")
    permissions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Relationships
    session = relationship("Session", back_populates="participants")
    user = relationship("User", back_populates="participants")

    def __repr__(self) -> str:
        return f"<Participant user={self.user_id} side={self.side.value}>"

