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
            from backend.services.llm_client import get_llm_client
            llm = get_llm_client()
            if llm is not None:
                orders = await self._llm_decide(
                    agent_data, doctrine, mission, knowledge, tick,
                    is_local=llm.is_local,
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
        is_local: bool = False,
    ) -> list[dict]:
        """Use LLM to make strategic decisions."""
        from backend.prompts.red_commander import build_red_commander_prompt
        from backend.services.llm_client import get_llm_client

        system_prompt, user_message = build_red_commander_prompt(
            agent_data, doctrine, mission, knowledge, tick,
            is_local=is_local,
        )

        llm = get_llm_client()
        if llm is None:
            raise RuntimeError("No LLM available for Red AI decisions")

        create_kwargs = dict(
            model=llm.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            temperature=0.7,
            max_completion_tokens=800 if is_local else 1500,
            response_format={"type": "json_object"},
        )
        # Local models may not support response_format
        if is_local:
            create_kwargs.pop("response_format", None)
        try:
            response = await llm.client.chat.completions.create(**create_kwargs)
        except Exception as api_err:
            err_str = str(api_err)
            if "max_tokens" in err_str or "max_completion_tokens" in err_str:
                create_kwargs.pop("max_completion_tokens", None)
                response = await llm.client.chat.completions.create(**create_kwargs)
            else:
                raise

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
        Deterministic rule-based decision engine applying tactical doctrine.

        Doctrine principles applied:
        - Fire and maneuver: artillery supports, infantry/armor assaults
        - Combined arms: unit types employed per their role
        - Terrain utilization: seek cover, use elevation advantage
        - Force preservation: withdraw when strength drops below threshold
        - Reconnaissance: recon units observe, don't fight
        - Mutual support: keep units within support distance

        Mission types:
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

        # ── Classify units by type for combined arms coordination ──
        artillery_units = []
        recon_units = []
        maneuver_units = []
        for unit in own_units:
            if unit.get("current_task"):
                task_type = unit["current_task"].get("type", "")
                # Skip units that already have active combat/movement tasks
                # unless they need to divert for critical reasons
                if task_type in ("move", "attack", "advance", "fire", "engage"):
                    if contacts and doctrine["advance_bias"] > 0.6:
                        nearest_contact = self._find_nearest_contact(unit, contacts)
                        if nearest_contact and nearest_contact.get("distance_to_nearest_m", 99999) < 500:
                            orders.append(self._make_engage_order(unit, nearest_contact, doctrine))
                    continue
            # Skip broken/offline/destroyed units
            if unit.get("morale", 1.0) < 0.15:
                continue
            if unit.get("comms_status") == "offline":
                continue
            # Retreat critically damaged units
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
                                "reasoning": f"Withdrawing — strength at {unit.get('strength', 0):.0%}, below retreat threshold",
                            })
                            continue
                continue

            # Classify by type
            utype = unit.get("unit_type", "")
            if any(k in utype for k in ("artillery", "mortar")):
                artillery_units.append(unit)
            elif any(k in utype for k in ("recon", "observation", "sniper")):
                recon_units.append(unit)
            else:
                maneuver_units.append(unit)

        # ── Apply doctrine by mission type ──
        if mission_type in ("hold", "defend"):
            # Doctrine: defense — recon observes, artillery supports, maneuver holds
            for unit in recon_units:
                orders.extend(self._decide_recon(unit, contacts, doctrine))
            for unit in artillery_units:
                orders.extend(self._decide_artillery_support(unit, contacts, maneuver_units, doctrine))
            for unit in maneuver_units:
                orders.extend(self._decide_hold(unit, contacts, doctrine))

        elif mission_type in ("patrol",):
            for unit in recon_units:
                orders.extend(self._decide_patrol(unit, contacts, doctrine, mission_waypoints, tick))
            for unit in artillery_units:
                orders.extend(self._decide_artillery_support(unit, contacts, maneuver_units, doctrine))
            for unit in maneuver_units:
                orders.extend(self._decide_patrol(unit, contacts, doctrine, mission_waypoints, tick))

        elif mission_type in ("attack", "advance"):
            # Doctrine: combined arms attack — recon finds, artillery suppresses, maneuver assaults
            for unit in recon_units:
                orders.extend(self._decide_recon(unit, contacts, doctrine))
            for unit in artillery_units:
                orders.extend(self._decide_artillery_support(unit, contacts, maneuver_units, doctrine))
            for unit in maneuver_units:
                orders.extend(self._decide_attack(unit, contacts, doctrine, mission_target))

        elif mission_type in ("withdraw", "retreat"):
            for unit in own_units:
                if unit.get("morale", 1.0) < 0.15 or unit.get("comms_status") == "offline":
                    continue
                orders.extend(self._decide_withdraw(unit, contacts, doctrine, mission_target))

        else:
            # Default: hold
            for unit in recon_units:
                orders.extend(self._decide_recon(unit, contacts, doctrine))
            for unit in artillery_units:
                orders.extend(self._decide_artillery_support(unit, contacts, maneuver_units, doctrine))
            for unit in maneuver_units:
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

    def _decide_recon(
        self,
        unit: dict,
        contacts: list[dict],
        doctrine: dict,
    ) -> list[dict]:
        """
        Recon/sniper/OP units: observe and report, maintain concealment.

        Doctrine: Recon pulls, doesn't push. These units should:
        - Observe enemy positions without engaging
        - Break contact (disengage) if enemy is too close
        - Never decisively engage — their value is intelligence
        """
        if not contacts:
            return []  # Stay concealed, observe

        nearest = self._find_nearest_contact(unit, contacts)
        if not nearest:
            return []

        dist = nearest.get("distance_to_nearest_m", 99999)

        # Enemy very close (< 400m) — disengage to maintain concealment
        if dist < 400:
            # Try to move away from the contact
            if unit.get("lat") and unit.get("lon") and nearest.get("lat") and nearest.get("lon"):
                # Move in opposite direction from contact
                dlat = unit["lat"] - nearest["lat"]
                dlon = unit["lon"] - nearest["lon"]
                # Normalize and move ~500m away
                import math
                d = math.sqrt(dlat*dlat + dlon*dlon) or 0.001
                escape_lat = unit["lat"] + (dlat / d) * 0.005  # ~500m
                escape_lon = unit["lon"] + (dlon / d) * 0.007
                return [{
                    "unit_id": unit["id"],
                    "order_type": "move",
                    "target_location": {"lat": escape_lat, "lon": escape_lon},
                    "speed": "fast",
                    "reasoning": f"Recon disengaging — enemy at {dist}m, maintaining concealment per doctrine",
                }]

        # Within observation range but safe — stay and observe (implicit, no order needed)
        return []

    def _decide_artillery_support(
        self,
        unit: dict,
        contacts: list[dict],
        supported_units: list[dict],
        doctrine: dict,
    ) -> list[dict]:
        """
        Artillery/mortar units: fire in support of maneuver elements.

        Doctrine: Artillery should support attacking units, not act independently.
        Priority targets:
        1. Contacts near friendly units under threat (< 1000m)
        2. Contacts that are closest to any friendly maneuver unit
        3. If no contacts, remain in position ready to fire
        """
        if not contacts:
            return []  # No targets — stay ready

        # Check ammo — can't fire without it
        if unit.get("ammo", 1.0) < 0.1:
            return []

        # Find highest priority target: enemy closest to a friendly maneuver unit
        best_target = None
        best_priority = float("inf")

        for contact in contacts:
            if not contact.get("lat") or not contact.get("lon"):
                continue

            # Check proximity to supported units
            min_dist_to_friendly = float("inf")
            for friendly in supported_units:
                if friendly.get("lat") and friendly.get("lon"):
                    d = _approx_distance_m(friendly["lat"], friendly["lon"],
                                           contact["lat"], contact["lon"])
                    min_dist_to_friendly = min(min_dist_to_friendly, d)

            # Priority: closer to friendlies = higher priority
            if min_dist_to_friendly < best_priority and min_dist_to_friendly < 3000:
                best_priority = min_dist_to_friendly
                best_target = contact

        if not best_target:
            # Fall back: target the nearest contact overall
            best_target = self._find_nearest_contact(unit, contacts)
            if not best_target:
                return []

        # Check if target is within fire range (approximate)
        if unit.get("lat") and unit.get("lon") and best_target.get("lat") and best_target.get("lon"):
            fire_dist = _approx_distance_m(unit["lat"], unit["lon"],
                                           best_target["lat"], best_target["lon"])
            # Typical artillery range: 3500-5000m, mortar: 2000-3500m
            max_range = 5000 if "artillery" in unit.get("unit_type", "") else 3500
            if fire_dist > max_range:
                return []  # Out of range — need to reposition (handled by attack/move logic)

        return [{
            "unit_id": unit["id"],
            "order_type": "attack",
            "target_location": {"lat": best_target["lat"], "lon": best_target["lon"]},
            "speed": "slow",
            "engagement_rules": "fire_at_will",
            "reasoning": (
                f"Fire support: suppressing {best_target.get('estimated_type', 'enemy')} "
                f"at {best_target.get('distance_to_nearest_m', '?')}m from nearest friendly. "
                f"Preparatory fires per combined arms doctrine."
            ),
        }]

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


