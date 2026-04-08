"""
Red AI Agent — core decision engine.

Decides what each Red unit should do based on:
  - Red commander's doctrine profile
  - Red commander's mission intent
  - Red-side knowledge state (contacts, own units, terrain)

Two modes:
  1. LLM-based (GPT-4.1): structured output → list of unit orders
  2. Rule-based fallback: deterministic heuristics when no LLM is available
"""

from __future__ import annotations

import json
import logging
from typing import Any

from backend.services.red_ai.doctrine import get_doctrine
from backend.services.red_ai.knowledge import _approx_distance_m

logger = logging.getLogger(__name__)


class RedAIAgent:
    """
    AI decision engine for a single Red commander agent.
    """

    async def decide(
        self,
        agent_data: dict,
        knowledge: dict,
        tick: int,
    ) -> list[dict]:
        """
        Make decisions for controlled units.

        Args:
            agent_data: RedAgent fields (doctrine_profile, mission_intent, risk_posture, etc.)
            knowledge: Built by knowledge.py — own_units, known_contacts, etc.
            tick: Current tick number

        Returns:
            List of order dicts: [{"unit_id": str, "order_type": str, "target_location": {...}, "speed": str, "reasoning": str}, ...]
        """
        posture = agent_data.get("risk_posture", "balanced")
        doctrine = get_doctrine(posture)
        mission = agent_data.get("mission_intent") or {}
        own_units = knowledge.get("own_units", [])
        contacts = knowledge.get("known_contacts", [])

        if not own_units:
            return []

        # Try LLM decision first
        try:
            from backend.config import settings
            if settings.OPENAI_API_KEY:
                orders = await self._llm_decide(
                    agent_data, doctrine, mission, knowledge, tick
                )
                if orders:
                    # Validate LLM output — only reference known unit IDs
                    valid_ids = {u["id"] for u in own_units}
                    validated = [o for o in orders if o.get("unit_id") in valid_ids]
                    if validated:
                        return validated
        except Exception as e:
            logger.warning("Red AI LLM decision failed, falling back to rules: %s", e)

        # Rule-based fallback
        return self._rule_based_decide(
            doctrine, mission, own_units, contacts, knowledge, tick
        )

    async def _llm_decide(
        self,
        agent_data: dict,
        doctrine: dict,
        mission: dict,
        knowledge: dict,
        tick: int,
    ) -> list[dict]:
        """Use LLM to make strategic decisions."""
        from backend.prompts.red_commander import build_red_commander_prompt
        from backend.config import settings
        import openai

        system_prompt, user_message = build_red_commander_prompt(
            agent_data, doctrine, mission, knowledge, tick
        )

        client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        response = await client.chat.completions.create(
            model=settings.OPENAI_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.7,
            max_tokens=1500,
            response_format={"type": "json_object"},
        )

        content = response.choices[0].message.content
        if not content:
            return []

        result = json.loads(content)
        orders = result.get("orders", [])

        # Validate with Pydantic schema
        from backend.schemas.red_agent import RedDecisionBatch
        try:
            batch = RedDecisionBatch.model_validate(result)
            orders = [d.model_dump(exclude_none=True) for d in batch.orders]
        except Exception:
            # Fall back to manual parsing if schema validation fails
            pass

        # Normalize output
        validated = []
        for o in orders:
            order = {
                "unit_id": o.get("unit_id", ""),
                "order_type": o.get("order_type", "move"),
                "speed": o.get("speed", "slow"),
            }
            if o.get("target_lat") is not None and o.get("target_lon") is not None:
                order["target_location"] = {
                    "lat": float(o["target_lat"]),
                    "lon": float(o["target_lon"]),
                }
            elif o.get("target_location"):
                order["target_location"] = o["target_location"]
            if o.get("engagement_rules"):
                order["engagement_rules"] = o["engagement_rules"]
            if o.get("reasoning"):
                order["reasoning"] = o["reasoning"]
            validated.append(order)

        return validated

    def _rule_based_decide(
        self,
        doctrine: dict,
        mission: dict,
        own_units: list[dict],
        contacts: list[dict],
        knowledge: dict,
        tick: int,
    ) -> list[dict]:
        """
        Deterministic rule-based decision engine.

        Behaviors based on mission type:
        - "hold"/"defend": Stay at position, engage contacts in range
        - "patrol": Cycle between waypoints
        - "attack"/"advance": Move toward known enemy contacts
        - "withdraw": Move away from enemy contacts

        Combined with doctrine parameters for fine-tuning.
        """
        orders = []
        mission_type = mission.get("type", "hold")
        mission_target = mission.get("target_location")
        mission_waypoints = mission.get("waypoints", [])
        terrain_around = knowledge.get("terrain_around_units", {})

        for unit in own_units:
            if unit.get("current_task"):
                # Unit already has a task — don't override unless critical
                task_type = unit["current_task"].get("type", "")
                if task_type in ("move", "attack", "advance"):
                    # Check if unit should divert due to new contact
                    if contacts and doctrine["advance_bias"] > 0.6:
                        nearest_contact = self._find_nearest_contact(unit, contacts)
                        if nearest_contact and nearest_contact.get("distance_to_nearest_m", 99999) < 500:
                            # Contact very close — engage
                            orders.append(self._make_engage_order(unit, nearest_contact, doctrine))
                    continue  # Keep current task

            # ── Check unit condition: skip if broken or comms offline ──
            if unit.get("morale", 1.0) < 0.15:
                continue  # Broken — not responding to orders
            if unit.get("comms_status") == "offline":
                continue  # Can't receive orders

            # ── Retreat if critically damaged regardless of mission ──
            if unit.get("strength", 1.0) < doctrine.get("retreat_threshold", 0.3):
                if mission_target:
                    rally_lat = mission_target.get("lat")
                    rally_lon = mission_target.get("lon")
                    if rally_lat and rally_lon and unit.get("lat") and unit.get("lon"):
                        dist = _approx_distance_m(unit["lat"], unit["lon"], rally_lat, rally_lon)
                        if dist > 100:
                            orders.append({
                                "unit_id": unit["id"],
                                "order_type": "move",
                                "target_location": {"lat": rally_lat, "lon": rally_lon},
                                "speed": "fast",
                                "reasoning": f"Withdrawing — strength at {unit.get('strength', 0):.0%}",
                            })
                            continue
                continue  # Too weak, no rally — hold

            # Unit is idle — assign based on mission
            if mission_type in ("hold", "defend"):
                orders.extend(self._decide_hold(unit, contacts, doctrine))

            elif mission_type in ("patrol",):
                orders.extend(self._decide_patrol(unit, contacts, doctrine, mission_waypoints, tick))

            elif mission_type in ("attack", "advance"):
                orders.extend(self._decide_attack(unit, contacts, doctrine, mission_target))

            elif mission_type in ("withdraw", "retreat"):
                orders.extend(self._decide_withdraw(unit, contacts, doctrine, mission_target))

            else:
                # Default: hold position
                orders.extend(self._decide_hold(unit, contacts, doctrine))

        return orders

    def _decide_hold(
        self,
        unit: dict,
        contacts: list[dict],
        doctrine: dict,
    ) -> list[dict]:
        """Hold position. Engage contacts if they come within range and doctrine allows."""
        if not contacts:
            return []  # Nothing to do — stay put

        nearest = self._find_nearest_contact(unit, contacts)
        if not nearest:
            return []

        dist = nearest.get("distance_to_nearest_m", 99999)
        det_range = unit.get("detection_range_m", 1500) * doctrine.get("engage_distance_factor", 1.0)

        # If contact is within engagement range and we're aggressive enough
        if dist < det_range and doctrine.get("advance_bias", 0.5) > 0.3:
            return [self._make_engage_order(unit, nearest, doctrine)]

        return []  # Hold and wait

    def _decide_patrol(
        self,
        unit: dict,
        contacts: list[dict],
        doctrine: dict,
        waypoints: list[dict],
        tick: int,
    ) -> list[dict]:
        """Patrol between waypoints. Engage contacts encountered."""
        # Check for contacts first — engage if aggressive
        if contacts:
            nearest = self._find_nearest_contact(unit, contacts)
            if nearest:
                dist = nearest.get("distance_to_nearest_m", 99999)
                if dist < 1000 and doctrine["risk_tolerance"] > 0.3:
                    return [self._make_engage_order(unit, nearest, doctrine)]

        if not waypoints:
            return []

        # Cycle through waypoints based on tick
        wp_index = (tick // 5) % len(waypoints)
        wp = waypoints[wp_index]
        wp_lat = wp.get("lat")
        wp_lon = wp.get("lon")

        if wp_lat is None or wp_lon is None:
            return []

        # Check if already near waypoint
        if unit.get("lat") and unit.get("lon"):
            dist_to_wp = _approx_distance_m(unit["lat"], unit["lon"], wp_lat, wp_lon)
            if dist_to_wp < 50:
                return []  # At waypoint, wait for next cycle

        return [{
            "unit_id": unit["id"],
            "order_type": "move",
            "target_location": {"lat": wp_lat, "lon": wp_lon},
            "speed": "slow",
            "reasoning": f"Patrol — moving to waypoint {wp_index + 1}",
        }]

    def _decide_attack(
        self,
        unit: dict,
        contacts: list[dict],
        doctrine: dict,
        mission_target: dict | None,
    ) -> list[dict]:
        """Advance toward enemy or mission target."""
        # Check unit condition — retreat if too weak
        if unit.get("strength", 1.0) < doctrine.get("retreat_threshold", 0.3):
            return []  # Too weak to attack — hold

        # Check ammo — don't attack if out
        if unit.get("ammo", 1.0) < 0.1:
            return []  # No ammo — hold

        # If we have contacts, attack nearest
        if contacts:
            nearest = self._find_nearest_contact(unit, contacts)
            if nearest and nearest.get("lat") and nearest.get("lon"):
                return [{
                    "unit_id": unit["id"],
                    "order_type": "attack",
                    "target_location": {"lat": nearest["lat"], "lon": nearest["lon"]},
                    "speed": "fast" if doctrine["advance_bias"] > 0.6 else "slow",
                    "engagement_rules": "fire_at_will",
                    "reasoning": f"Engaging {nearest.get('estimated_type', 'enemy')} at {nearest.get('distance_to_nearest_m', '?')}m",
                }]

        # No contacts — advance to mission target
        if mission_target:
            target_lat = mission_target.get("lat")
            target_lon = mission_target.get("lon")
            if target_lat is not None and target_lon is not None:
                if unit.get("lat") and unit.get("lon"):
                    dist = _approx_distance_m(unit["lat"], unit["lon"], target_lat, target_lon)
                    if dist < 50:
                        return []  # Already at target
                return [{
                    "unit_id": unit["id"],
                    "order_type": "move",
                    "target_location": {"lat": target_lat, "lon": target_lon},
                    "speed": "fast" if doctrine["advance_bias"] > 0.6 else "slow",
                    "reasoning": "Advancing to mission objective",
                }]

        return []

    def _decide_withdraw(
        self,
        unit: dict,
        contacts: list[dict],
        doctrine: dict,
        mission_target: dict | None,
    ) -> list[dict]:
        """Withdraw from enemy toward rally point."""
        rally = mission_target
        if not rally:
            return []

        rally_lat = rally.get("lat")
        rally_lon = rally.get("lon")
        if rally_lat is None or rally_lon is None:
            return []

        if unit.get("lat") and unit.get("lon"):
            dist = _approx_distance_m(unit["lat"], unit["lon"], rally_lat, rally_lon)
            if dist < 50:
                return []  # Already at rally

        return [{
            "unit_id": unit["id"],
            "order_type": "move",
            "target_location": {"lat": rally_lat, "lon": rally_lon},
            "speed": "fast",
            "reasoning": "Withdrawing to rally point",
        }]

    def _find_nearest_contact(
        self,
        unit: dict,
        contacts: list[dict],
    ) -> dict | None:
        """Find the nearest contact to the given unit."""
        if not unit.get("lat") or not unit.get("lon") or not contacts:
            return None

        nearest = None
        nearest_dist = float("inf")

        for c in contacts:
            if not c.get("lat") or not c.get("lon"):
                continue
            dist = _approx_distance_m(unit["lat"], unit["lon"], c["lat"], c["lon"])
            if dist < nearest_dist:
                nearest_dist = dist
                nearest = {**c, "distance_to_nearest_m": round(dist)}

        return nearest

    def _make_engage_order(
        self,
        unit: dict,
        contact: dict,
        doctrine: dict,
    ) -> dict:
        """Create an attack order toward a contact."""
        return {
            "unit_id": unit["id"],
            "order_type": "attack",
            "target_location": {"lat": contact["lat"], "lon": contact["lon"]},
            "speed": "fast" if doctrine["advance_bias"] > 0.6 else "slow",
            "engagement_rules": "fire_at_will",
            "reasoning": f"Engaging {contact.get('estimated_type', 'enemy')} at {contact.get('distance_to_nearest_m', '?')}m",
        }


# Singleton
red_ai_agent = RedAIAgent()


