"""
Pydantic v2 schemas for Red AI commander agents.

Used for API request/response validation and structured output parsing.
"""

from __future__ import annotations

import enum
from typing import Optional

from pydantic import BaseModel, Field


class RiskPostureEnum(str, enum.Enum):
    aggressive = "aggressive"
    balanced = "balanced"
    cautious = "cautious"
    defensive = "defensive"


class MissionTypeEnum(str, enum.Enum):
    hold = "hold"
    defend = "defend"
    patrol = "patrol"
    attack = "attack"
    advance = "advance"
    withdraw = "withdraw"
    retreat = "retreat"


# ── Request schemas ──────────────────────────────────────────

class RedAgentCreate(BaseModel):
    name: str = Field("Red Commander", max_length=100)
    risk_posture: RiskPostureEnum = RiskPostureEnum.balanced
    mission_intent: Optional[dict] = None
    controlled_unit_ids: Optional[list[str]] = Field(
        None,
        description="List of unit UUIDs to control. If None, controls all Red units.",
    )


class RedAgentUpdate(BaseModel):
    name: Optional[str] = Field(None, max_length=100)
    risk_posture: Optional[RiskPostureEnum] = None
    mission_intent: Optional[dict] = None
    controlled_unit_ids: Optional[list[str]] = None


# ── Response schemas ─────────────────────────────────────────

class KnowledgeSummary(BaseModel):
    """Summary stats from the Red AI knowledge builder."""
    total_units: int = 0
    avg_strength: float = 0.0
    avg_morale: float = 0.0
    total_contacts: int = 0
    units_idle: int = 0
    units_moving: int = 0


class DecisionState(BaseModel):
    """Debug snapshot of the last Red AI decision cycle."""
    tick: int = 0
    decisions_count: int = 0
    decisions: list[dict] = Field(default_factory=list)
    contacts_known: int = 0
    units_controlled: int = 0
    forced: Optional[bool] = None


class RedAgentRead(BaseModel):
    """Serialized Red AI agent for API responses."""
    id: str
    session_id: str
    name: str
    risk_posture: str
    doctrine_profile: Optional[dict] = None
    mission_intent: Optional[dict] = None
    controlled_unit_ids: Optional[list[str]] = None
    knowledge_state: Optional[dict] = None
    last_decision_tick: int = 0
    decision_state: Optional[dict] = None


# ── Red AI decision output (LLM structured output) ──────────

class RedDecision(BaseModel):
    """
    A single order decision made by a Red AI agent.

    This is the validated output format — all fields are constrained.
    The LLM must produce decisions matching this schema.
    """
    unit_id: str = Field(
        ...,
        description="UUID of the unit to issue the order to. Must be a controlled unit.",
    )
    order_type: str = Field(
        ...,
        description="One of: move, attack, defend, observe, halt, withdraw",
    )
    target_location: Optional[dict] = Field(
        None,
        description='Target location as {"lat": float, "lon": float}. Required for move/attack.',
    )
    speed: str = Field(
        "slow",
        description="Movement speed: 'slow' (cautious/tactical) or 'fast' (rapid).",
    )
    engagement_rules: Optional[str] = Field(
        None,
        description="One of: fire_at_will, hold_fire, return_fire_only",
    )
    reasoning: Optional[str] = Field(
        None,
        description="Brief reasoning for this decision (for debugging/AAR).",
    )


class RedDecisionBatch(BaseModel):
    """
    Full output of a Red AI decision cycle.
    The LLM returns this JSON object.
    """
    orders: list[RedDecision] = Field(default_factory=list)
    overall_assessment: Optional[str] = Field(
        None,
        description="Brief assessment of the tactical situation.",
    )


class ForceDecideResult(BaseModel):
    """Result of a forced Red AI decision."""
    agent_id: str
    decisions: list[dict] = Field(default_factory=list)
    orders_created: int = 0
    knowledge_summary: Optional[dict] = None

