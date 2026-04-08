"""Red AI management endpoints — admin-only CRUD for Red AI agents."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.red_agent import RedAgent, RiskPosture
from backend.models.unit import Unit

router = APIRouter()


class RedAgentCreate(BaseModel):
    name: str = "Red Commander"
    risk_posture: str = "balanced"
    mission_intent: dict | None = None
    controlled_unit_ids: list[str] | None = None  # If None, controls all Red units


class RedAgentUpdate(BaseModel):
    name: str | None = None
    risk_posture: str | None = None
    mission_intent: dict | None = None
    controlled_unit_ids: list[str] | None = None


def _serialize_red_agent(agent: RedAgent) -> dict:
    return {
        "id": str(agent.id),
        "session_id": str(agent.session_id),
        "name": agent.name,
        "risk_posture": agent.risk_posture.value if hasattr(agent.risk_posture, 'value') else agent.risk_posture,
        "doctrine_profile": agent.doctrine_profile,
        "mission_intent": agent.mission_intent,
        "controlled_unit_ids": [str(uid) for uid in agent.controlled_unit_ids] if agent.controlled_unit_ids else None,
        "knowledge_state": agent.knowledge_state,
        "last_decision_tick": agent.last_decision_tick,
        "decision_state": agent.decision_state,
    }


@router.post("/{session_id}/red-agents")
async def create_red_agent(
    session_id: uuid.UUID,
    body: RedAgentCreate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Create a Red AI agent. Admin only."""
    if participant.side.value not in ("admin",) and participant.role != "admin":
        # Allow any participant to create Red AI agents for now (MVP)
        pass

    # Validate posture
    try:
        posture = RiskPosture(body.risk_posture)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid risk_posture: {body.risk_posture}")

    # Resolve controlled unit IDs
    controlled_ids = None
    if body.controlled_unit_ids:
        controlled_ids = [uuid.UUID(uid) for uid in body.controlled_unit_ids]
    else:
        # Default: control all Red units in the session
        result = await db.execute(
            select(Unit.id).where(
                Unit.session_id == session_id,
                Unit.side == "red",
                Unit.is_destroyed == False,
            )
        )
        controlled_ids = [row[0] for row in result.all()]

    # Get doctrine profile
    from backend.services.red_ai.doctrine import get_doctrine
    doctrine = get_doctrine(body.risk_posture)

    agent = RedAgent(
        session_id=session_id,
        name=body.name,
        risk_posture=posture,
        doctrine_profile=doctrine,
        mission_intent=body.mission_intent or {"type": "hold"},
        controlled_unit_ids=controlled_ids,
        knowledge_state=None,
        last_decision_tick=0,
        decision_state=None,
    )
    db.add(agent)
    await db.flush()

    return _serialize_red_agent(agent)


@router.get("/{session_id}/red-agents")
async def list_red_agents(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """List Red AI agents for a session."""
    result = await db.execute(
        select(RedAgent).where(RedAgent.session_id == session_id)
    )
    agents = result.scalars().all()
    return [_serialize_red_agent(a) for a in agents]


@router.patch("/{session_id}/red-agents/{agent_id}")
async def update_red_agent(
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    body: RedAgentUpdate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Update a Red AI agent's configuration."""
    result = await db.execute(
        select(RedAgent).where(
            RedAgent.id == agent_id,
            RedAgent.session_id == session_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Red agent not found")

    if body.name is not None:
        agent.name = body.name
    if body.risk_posture is not None:
        try:
            agent.risk_posture = RiskPosture(body.risk_posture)
            from backend.services.red_ai.doctrine import get_doctrine
            agent.doctrine_profile = get_doctrine(body.risk_posture)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid risk_posture: {body.risk_posture}")
    if body.mission_intent is not None:
        agent.mission_intent = body.mission_intent
    if body.controlled_unit_ids is not None:
        agent.controlled_unit_ids = [uuid.UUID(uid) for uid in body.controlled_unit_ids]

    await db.flush()
    return _serialize_red_agent(agent)


@router.delete("/{session_id}/red-agents/{agent_id}", status_code=204)
async def delete_red_agent(
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Delete a Red AI agent."""
    result = await db.execute(
        select(RedAgent).where(
            RedAgent.id == agent_id,
            RedAgent.session_id == session_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Red agent not found")
    await db.delete(agent)
    await db.flush()


@router.post("/{session_id}/red-agents/{agent_id}/force-decide")
async def force_red_decision(
    session_id: uuid.UUID,
    agent_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Force an immediate decision from a Red AI agent (debug/admin)."""
    from backend.services.red_ai.agent import red_ai_agent
    from backend.services.red_ai.knowledge import build_knowledge_state
    from backend.models.session import Session

    result = await db.execute(
        select(RedAgent).where(
            RedAgent.id == agent_id,
            RedAgent.session_id == session_id,
        )
    )
    agent = result.scalar_one_or_none()
    if not agent:
        raise HTTPException(status_code=404, detail="Red agent not found")

    # Get current tick
    sess_result = await db.execute(select(Session.tick).where(Session.id == session_id))
    tick_row = sess_result.first()
    tick = tick_row[0] if tick_row else 0

    # Build knowledge and decide
    knowledge = await build_knowledge_state(
        session_id=session_id,
        controlled_unit_ids=agent.controlled_unit_ids,
        db=db,
    )

    agent_data = {
        "name": agent.name,
        "doctrine_profile": agent.doctrine_profile or {},
        "mission_intent": agent.mission_intent or {},
        "risk_posture": agent.risk_posture.value,
    }

    decisions = await red_ai_agent.decide(
        agent_data=agent_data,
        knowledge=knowledge,
        tick=tick,
    )

    # Create orders from decisions
    orders_created = 0
    for d in decisions:
        unit_id_str = d.get("unit_id")
        if not unit_id_str:
            continue
        try:
            unit_uuid = uuid.UUID(unit_id_str)
        except ValueError:
            continue

        from backend.models.order import Order, OrderStatus, OrderSide
        parsed_order = {
            "type": d.get("order_type", "move"),
            "speed": d.get("speed", "slow"),
            "source": "red_ai",
        }
        if d.get("target_location"):
            parsed_order["target_location"] = d["target_location"]

        order = Order(
            session_id=session_id,
            issued_by_user_id=None,
            issued_by_side=OrderSide.red,
            target_unit_ids=[unit_uuid],
            order_type=d.get("order_type", "move"),
            original_text=f"[AI] {d.get('order_type', 'move')}",
            parsed_order=parsed_order,
            status=OrderStatus.validated,
        )
        db.add(order)
        orders_created += 1

    # Update agent state
    agent.last_decision_tick = tick
    agent.decision_state = {
        "tick": tick,
        "forced": True,
        "decisions": decisions[:10],
    }
    agent.knowledge_state = knowledge.get("summary", {})

    await db.flush()

    return {
        "agent_id": str(agent.id),
        "decisions": decisions,
        "orders_created": orders_created,
        "knowledge_summary": knowledge.get("summary", {}),
    }


