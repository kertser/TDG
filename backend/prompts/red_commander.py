"""
System prompt and user message construction for Red AI commander LLM calls.

The prompt is carefully designed to:
1. Never leak Blue-side information (only Red knowledge state is included)
2. Constrain output to valid unit IDs and actionable order types
3. Respect the doctrine profile
4. Output structured JSON for reliable parsing
5. Include terrain and grid context for better spatial reasoning
"""

from __future__ import annotations

import json


def build_red_commander_prompt(
    agent_data: dict,
    doctrine: dict,
    mission: dict,
    knowledge: dict,
    tick: int,
) -> tuple[str, str]:
    """
    Build system prompt and user message for a Red AI commander LLM call.

    Returns:
        (system_prompt, user_message) tuple
    """
    # ── System Prompt ─────────────────────────────────────
    system_prompt = f"""You are a Red force military commander in a tactical exercise.
Your name: {agent_data.get('name', 'Red Commander')}

{doctrine.get('prompt_instruction', 'You are a balanced commander.')}

MISSION: {json.dumps(mission, default=str) if mission else 'No specific mission assigned. Defend your current positions.'}

You must respond with a JSON object containing an "orders" array.
Each order must have:
- "unit_id": string (exact UUID from the unit list below)
- "order_type": one of "move", "attack", "defend", "observe", "halt", "withdraw"
- "target_lat": float (latitude) — required for move/attack
- "target_lon": float (longitude) — required for move/attack
- "speed": "slow" or "fast"
- "engagement_rules": optional, one of "fire_at_will", "hold_fire", "return_fire_only"
- "reasoning": optional, brief explanation of why this order (for after-action review)

Rules:
1. You can ONLY issue orders to units listed in YOUR UNITS below.
2. You can ONLY reference locations near the operational area.
3. You do NOT know the exact positions of enemy units — only the contacts detected by your forces.
4. Make tactically sound decisions based on your doctrine and available information.
5. If you have no changes to make, return {{"orders": []}}.
6. Keep orders concise — one order per unit maximum.
7. Do not order destroyed or unavailable units.
8. Consider terrain when choosing movement routes and positions.
9. Use elevation advantage when possible (higher ground for defense/observation).
10. Conserve ammunition — don't attack without clear purpose.

Example response:
{{"orders": [
  {{"unit_id": "abc-123", "order_type": "move", "target_lat": 49.05, "target_lon": 4.50, "speed": "slow", "reasoning": "Advance to high ground for better observation"}},
  {{"unit_id": "def-456", "order_type": "attack", "target_lat": 49.06, "target_lon": 4.51, "speed": "fast", "engagement_rules": "fire_at_will", "reasoning": "Engage detected enemy infantry"}}
]}}"""

    # ── User Message (current state) ──────────────────────
    own_units = knowledge.get("own_units", [])
    contacts = knowledge.get("known_contacts", [])
    summary = knowledge.get("summary", {})
    terrain_around = knowledge.get("terrain_around_units", {})
    elevation_data = knowledge.get("elevation_at_units", {})

    units_text = "YOUR UNITS:\n"
    for u in own_units:
        task_desc = ""
        if u.get("current_task"):
            t = u["current_task"]
            task_desc = f" [TASK: {t.get('type', '?')}"
            if t.get("target_snail"):
                task_desc += f" → {t['target_snail']}"
            task_desc += "]"

        pos_desc = f"({u.get('lat', '?'):.4f}, {u.get('lon', '?'):.4f})" if u.get("lat") else "unknown position"
        grid_ref = u.get("grid_ref", "")
        grid_desc = f" grid:{grid_ref}" if grid_ref else ""

        # Terrain at this unit's position
        terrain_desc = ""
        if grid_ref and grid_ref in terrain_around:
            t_info = terrain_around[grid_ref]
            terrain_desc = f" terrain:{t_info.get('terrain_type', '?')}"
            if t_info.get("elevation_m") is not None:
                terrain_desc += f" elev:{t_info['elevation_m']}m"
        elif grid_ref:
            # Try parent path
            parent = grid_ref.rsplit("-", 1)[0] if "-" in grid_ref else ""
            if parent and parent in terrain_around:
                t_info = terrain_around[parent]
                terrain_desc = f" terrain:{t_info.get('terrain_type', '?')}"

        # Status indicators
        status_flags = []
        if u.get("suppression", 0) > 0.3:
            status_flags.append("SUPPRESSED")
        if u.get("ammo", 1.0) < 0.3:
            status_flags.append("LOW_AMMO")
        if u.get("morale", 1.0) < 0.3:
            status_flags.append("LOW_MORALE")
        if u.get("comms_status") == "offline":
            status_flags.append("COMMS_OUT")
        flags_desc = f" ⚠{','.join(status_flags)}" if status_flags else ""

        units_text += (
            f"- {u['name']} (ID: {u['id']}) | type: {u.get('unit_type', '?')} | "
            f"pos: {pos_desc}{grid_desc} | str: {u.get('strength', 1.0):.0%} | "
            f"ammo: {u.get('ammo', 1.0):.0%} | morale: {u.get('morale', 1.0):.0%}"
            f"{terrain_desc}{task_desc}{flags_desc}\n"
        )

    contacts_text = "\nDETECTED ENEMY CONTACTS:\n"
    if contacts:
        for c in contacts:
            pos_desc = f"({c.get('lat', '?'):.4f}, {c.get('lon', '?'):.4f})" if c.get("lat") else "unknown"
            dist_desc = f" ({c.get('distance_to_nearest_m', '?')}m away)" if c.get("distance_to_nearest_m") else ""
            bearing_desc = f" bearing:{c.get('bearing_from_nearest_deg', '?')}°" if c.get("bearing_from_nearest_deg") else ""
            grid_desc = f" grid:{c['grid_ref']}" if c.get("grid_ref") else ""
            contacts_text += (
                f"- {c.get('estimated_type', 'unknown')} | pos: {pos_desc}{grid_desc}{dist_desc}{bearing_desc} | "
                f"confidence: {c.get('confidence', 0):.0%} | source: {c.get('source', '?')}\n"
            )
    else:
        contacts_text += "- No enemy contacts detected.\n"

    situation_text = f"\nSITUATION SUMMARY (Turn {tick}):\n"
    situation_text += f"- Total units: {summary.get('total_units', 0)}\n"
    situation_text += f"- Average strength: {summary.get('avg_strength', 0):.0%}\n"
    situation_text += f"- Average morale: {summary.get('avg_morale', 0):.0%}\n"
    situation_text += f"- Average ammo: {summary.get('avg_ammo', 0):.0%}\n"
    situation_text += f"- Known enemy contacts: {summary.get('total_contacts', 0)}\n"
    situation_text += f"- Idle units: {summary.get('units_idle', 0)}\n"
    situation_text += f"- Units in motion: {summary.get('units_moving', 0)}\n"
    situation_text += f"- Units attacking: {summary.get('units_attacking', 0)}\n"
    situation_text += f"- Units defending: {summary.get('units_defending', 0)}\n"

    # Terrain info
    terrain_types = knowledge.get("terrain_types_present", [])
    if terrain_types:
        situation_text += f"- Terrain in area: {', '.join(terrain_types)}\n"

    # Discovered objects
    objects = knowledge.get("discovered_objects", [])
    if objects:
        obj_descriptions = []
        for o in objects[:8]:
            desc = o.get("type", "?")
            if o.get("grid_ref"):
                desc += f" at {o['grid_ref']}"
            obj_descriptions.append(desc)
        situation_text += f"- Discovered objects: {', '.join(obj_descriptions)}\n"

    user_message = (
        f"Current turn: {tick}\n\n"
        f"{units_text}\n{contacts_text}\n{situation_text}\n"
        f"Based on your mission and doctrine, issue orders for your units. "
        f"Respond with a JSON object containing an 'orders' array."
    )

    return system_prompt, user_message

