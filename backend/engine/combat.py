"""
Combat resolution engine.

Uses formulas from AGENTS.MD Section 8.4:
  fire_effectiveness = base_firepower × strength × ammo_factor × (1 - suppression) × terrain_mod
  damage = fire_effectiveness × DAMAGE_SCALAR / target_protection
  suppression_inflicted = fire_effectiveness × 0.03
"""

from __future__ import annotations

import math
import uuid

from geoalchemy2.shape import to_shape
from backend.engine.terrain import TerrainService

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Base firepower by unit type
BASE_FIREPOWER = {
    "infantry_platoon": 10,
    "tank_company": 30,
    "mortar_section": 20,
    "mortar": 20,
    "at_team": 15,
    "recon_team": 5,
    "observation_post": 2,
}

# Default weapon range by unit type (meters)
WEAPON_RANGE = {
    "infantry_platoon": 800,
    "tank_company": 2500,
    "mortar_section": 4000,
    "mortar": 4000,
    "at_team": 3000,
    "recon_team": 600,
    "observation_post": 400,
}

DAMAGE_SCALAR = 0.02  # ~2% strength loss per tick under sustained fire


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _ammo_factor(ammo: float) -> float:
    if ammo > 0.5:
        return 1.0
    elif ammo >= 0.2:
        return 0.7
    else:
        return 0.3


def _get_position(unit) -> tuple[float, float] | None:
    if unit.position is None:
        return None
    try:
        pt = to_shape(unit.position)
        return pt.y, pt.x  # lat, lon
    except Exception:
        return None


def process_combat(
    all_units: list,
    terrain: TerrainService,
) -> tuple[list[dict], set[uuid.UUID]]:
    """
    Resolve combat for all units with attack/engage tasks.

    Returns list of event dicts.
    """
    events = []
    # Track which units are under fire this tick (for suppression recovery)
    under_fire: set[uuid.UUID] = set()

    for attacker in all_units:
        if attacker.is_destroyed:
            continue

        task = attacker.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        if task_type not in ("attack", "engage", "fire"):
            continue

        # Find the target
        target_id = task.get("target_unit_id")
        target_location = task.get("target_location")

        atk_pos = _get_position(attacker)
        if atk_pos is None:
            continue

        target = None
        if target_id:
            for u in all_units:
                if str(u.id) == str(target_id) and not u.is_destroyed:
                    target = u
                    break

        if target is None:
            continue

        tgt_pos = _get_position(target)
        if tgt_pos is None:
            continue

        # Check range
        dist = _distance_m(atk_pos[0], atk_pos[1], tgt_pos[0], tgt_pos[1])
        weapon_range = WEAPON_RANGE.get(attacker.unit_type, 800)
        # Check for special ranges (e.g., ATGM, mortar)
        caps = attacker.capabilities or {}
        if caps.get("atgm_range_m"):
            weapon_range = max(weapon_range, caps["atgm_range_m"])
        if caps.get("mortar_range_m"):
            weapon_range = max(weapon_range, caps["mortar_range_m"])

        if dist > weapon_range:
            continue  # Out of range

        # Calculate fire effectiveness
        base_fp = BASE_FIREPOWER.get(attacker.unit_type, 5)
        strength = attacker.strength or 1.0
        ammo = attacker.ammo or 1.0
        suppression = attacker.suppression or 0.0

        terrain_mod = terrain.attack_modifier(atk_pos[1], atk_pos[0])

        fire_effectiveness = (
            base_fp
            * strength
            * _ammo_factor(ammo)
            * (1.0 - suppression)
            * terrain_mod
        )

        # Target protection
        tgt_terrain = terrain.protection_factor(tgt_pos[1], tgt_pos[0])
        tgt_task = target.current_task or {}
        if tgt_task.get("type") == "dig_in":
            tgt_protection = 2.0
        else:
            tgt_protection = tgt_terrain

        # Apply damage
        damage = fire_effectiveness * DAMAGE_SCALAR / tgt_protection
        target.strength = max(0.0, (target.strength or 1.0) - damage)

        # Apply suppression
        suppression_inflicted = fire_effectiveness * 0.03
        target.suppression = min(1.0, (target.suppression or 0.0) + suppression_inflicted)

        under_fire.add(target.id)

        # Check for destruction
        if target.strength <= 0.01:
            target.is_destroyed = True
            target.current_task = None
            events.append({
                "event_type": "unit_destroyed",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": f"{target.name} destroyed by {attacker.name}",
                "payload": {"attacker": str(attacker.id), "target": str(target.id)},
            })
        else:
            events.append({
                "event_type": "combat",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": (
                    f"{attacker.name} engaging {target.name} "
                    f"(dmg={damage:.3f}, supp={suppression_inflicted:.3f})"
                ),
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "damage": round(damage, 4),
                    "suppression": round(suppression_inflicted, 4),
                    "target_strength": round(target.strength, 4),
                    "distance_m": round(dist, 1),
                },
            })

    return events, under_fire


