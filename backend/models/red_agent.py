"""RedAgent model – AI-controlled enemy commander agents."""

from __future__ import annotations

import enum
import uuid

from sqlalchemy import String, Integer, ForeignKey, Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class RiskPosture(str, enum.Enum):
    aggressive = "aggressive"
    balanced = "balanced"
    cautious = "cautious"
    defensive = "defensive"


class RedAgent(Base):
    __tablename__ = "red_agents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    doctrine_profile: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    mission_intent: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    controlled_unit_ids = mapped_column(ARRAY(UUID(as_uuid=True)), nullable=True)
    knowledge_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    risk_posture: Mapped[RiskPosture] = mapped_column(
        SAEnum(RiskPosture, name="risk_posture_enum", create_constraint=True),
        default=RiskPosture.balanced,
    )
    last_decision_tick: Mapped[int] = mapped_column(Integer, default=0)
    decision_state: Mapped[dict | None] = mapped_column(JSONB, nullable=True)

    # Relationships
    session = relationship("Session", back_populates="red_agents")

    def __repr__(self) -> str:
        return f"<RedAgent {self.name!r} posture={self.risk_posture.value}>"

