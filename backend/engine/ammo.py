"""
Ammo consumption engine.

AGENTS.MD Section 8.9:
  each tick a unit fires: unit.ammo -= 0.01 × fire_rate_modifier
  if unit.ammo <= 0: unit cannot fire, generate Event('ammo_depleted')
"""

from __future__ import annotations

import uuid


FIRE_RATE_MODIFIER = {
    "infantry_platoon": 1.0,
    "tank_company": 1.5,
    "mortar_section": 2.0,
    "mortar": 2.0,
    "at_team": 0.5,
    "recon_team": 0.5,
    "observation_post": 0.2,
}


def process_ammo(
    all_units: list,
    under_fire: set[uuid.UUID],
) -> list[dict]:
    """
    Consume ammo for units that fired this tick.

    Units with attack/engage/fire tasks consume ammo.

    Returns list of event dicts.
    """
    events = []

    for unit in all_units:
        if unit.is_destroyed:
            continue

        task = unit.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        if task_type not in ("attack", "engage", "fire"):
            continue

        ammo = unit.ammo if unit.ammo is not None else 1.0
        if ammo <= 0:
            continue  # Already depleted, can't fire

        rate = FIRE_RATE_MODIFIER.get(unit.unit_type, 1.0)
        consumption = 0.01 * rate
        new_ammo = max(0.0, ammo - consumption)
        unit.ammo = new_ammo

        if new_ammo <= 0:
            events.append({
                "event_type": "ammo_depleted",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} ammunition depleted!",
                "payload": {"unit_type": unit.unit_type},
            })

    return events

