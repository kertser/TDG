"""
Red AI Runner — tick-integrated runner that activates Red AI agents.

Called from tick.py BEFORE _process_orders so that Red AI decisions
(which create Order records) get processed in the same tick.

Conditions for Red AI to run:
  1. Session has RedAgent records
  2. No human players on the Red side, OR session has red_ai_enabled=True
  3. Enough ticks have passed since last decision (configurable interval)
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.red_agent import RedAgent
from backend.models.order import Order, OrderStatus, OrderSide
from backend.models.session import SessionParticipant, Side

logger = logging.getLogger(__name__)

# How often Red AI makes decisions (in ticks)
RED_AI_DECISION_INTERVAL = 3


async def run_red_agents(
    session_id: uuid.UUID,
    tick: int,
    db: AsyncSession,
) -> list[dict]:
    """
    Run all Red AI agents for the session.

    Returns list of event dicts for logging.
    """
    events = []

    # ── Check if Red AI should run ────────────────────────
    # Load Red agents
    result = await db.execute(
        select(RedAgent).where(RedAgent.session_id == session_id)
    )
    red_agents = list(result.scalars().all())

    if not red_agents:
        return events  # No Red AI configured

    # Check if there are human Red players — if so, AI is secondary
    human_result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.side == Side.red,
        )
    )
    human_red_players = list(human_result.scalars().all())
    has_human_red = len(human_red_players) > 0

    # ── Load grid service for snail path resolution ──────
    grid_service = None
    try:
        from backend.models.grid import GridDefinition
        from backend.services.grid_service import GridService
        gd_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        gd = gd_result.scalar_one_or_none()
        if gd:
            grid_service = GridService(gd)
    except Exception:
        pass

    # ── Process each Red agent ────────────────────────────
    from backend.services.red_ai.agent import red_ai_agent
    from backend.services.red_ai.knowledge import build_knowledge_state

    for agent in red_agents:
        # Check decision interval
        ticks_since = tick - (agent.last_decision_tick or 0)
        interval = RED_AI_DECISION_INTERVAL
        if has_human_red:
            interval = interval * 2  # Slower when human is playing Red

        if ticks_since < interval:
            continue

        try:
            # Build knowledge state (Red-side only)
            knowledge = await build_knowledge_state(
                session_id=session_id,
                controlled_unit_ids=agent.controlled_unit_ids,
                db=db,
                grid_service=grid_service,
            )

            # Make decisions
            agent_data = {
                "name": agent.name,
                "doctrine_profile": agent.doctrine_profile or {},
                "mission_intent": agent.mission_intent or {},
                "risk_posture": agent.risk_posture.value if hasattr(agent.risk_posture, 'value') else (agent.risk_posture or "balanced"),
                "knowledge_state": agent.knowledge_state,
            }

            decisions = await red_ai_agent.decide(
                agent_data=agent_data,
                knowledge=knowledge,
                tick=tick,
            )

            # Create Order records for each decision
            orders_created = 0
            for decision in decisions:
                unit_id_str = decision.get("unit_id")
                if not unit_id_str:
                    continue

                try:
                    unit_uuid = uuid.UUID(unit_id_str)
                except ValueError:
                    continue

                order_type = decision.get("order_type", "move")
                target_loc = decision.get("target_location")
                speed = decision.get("speed", "slow")
                reasoning = decision.get("reasoning", "")

                parsed_order = {
                    "type": order_type,
                    "speed": speed,
                    "source": "red_ai",
                }
                if target_loc:
                    parsed_order["target_location"] = target_loc
                    # Resolve target to snail path if possible
                    if grid_service and target_loc.get("lat") and target_loc.get("lon"):
                        try:
                            snail = grid_service.point_to_snail(
                                target_loc["lat"], target_loc["lon"], depth=2
                            )
                            if snail:
                                parsed_order["target_snail"] = snail
                        except Exception:
                            pass

                if decision.get("engagement_rules"):
                    parsed_order["engagement_rules"] = decision["engagement_rules"]

                # Build description text
                target_desc = ""
                if target_loc:
                    target_desc = f" to ({target_loc.get('lat', '?'):.4f}, {target_loc.get('lon', '?'):.4f})"
                    if parsed_order.get("target_snail"):
                        target_desc += f" [{parsed_order['target_snail']}]"

                reason_desc = f" — {reasoning}" if reasoning else ""

                order = Order(
                    session_id=session_id,
                    issued_by_user_id=None,  # AI-issued
                    issued_by_side=OrderSide.red,
                    target_unit_ids=[unit_uuid],
                    order_type=order_type,
                    original_text=f"[AI] {order_type}{target_desc} ({speed}){reason_desc}",
                    parsed_order=parsed_order,
                    status=OrderStatus.validated,
                )
                db.add(order)
                orders_created += 1

            # Update agent state
            agent.last_decision_tick = tick
            agent.knowledge_state = knowledge.get("summary", {})
            agent.decision_state = {
                "tick": tick,
                "decisions_count": len(decisions),
                "decisions": decisions[:10],  # Store last decisions for debug
                "contacts_known": len(knowledge.get("known_contacts", [])),
                "units_controlled": len(knowledge.get("own_units", [])),
                "terrain_types": knowledge.get("terrain_types_present", []),
            }

            if orders_created > 0:
                events.append({
                    "event_type": "red_ai_decision",
                    "text_summary": f"Red AI '{agent.name}' issued {orders_created} orders (posture: {agent_data['risk_posture']})",
                    "payload": {
                        "agent_id": str(agent.id),
                        "agent_name": agent.name,
                        "orders_count": orders_created,
                        "posture": agent_data["risk_posture"],
                    },
                })

            logger.info(
                "Red AI '%s' made %d decisions at tick %d (posture=%s, contacts=%d)",
                agent.name, len(decisions), tick,
                agent_data["risk_posture"],
                len(knowledge.get("known_contacts", [])),
            )

        except Exception as e:
            logger.error("Red AI agent '%s' decision failed: %s", agent.name, e, exc_info=True)
            events.append({
                "event_type": "red_ai_error",
                "text_summary": f"Red AI '{agent.name}' decision failed: {str(e)[:100]}",
                "payload": {"agent_id": str(agent.id), "error": str(e)[:200]},
            })

    return events




