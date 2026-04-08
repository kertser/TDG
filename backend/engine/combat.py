"""
Combat resolution engine.

Uses formulas from AGENTS.MD Section 8.4:
  fire_effectiveness = base_firepower × strength × ammo_factor × (1 - suppression) × terrain_mod
  damage = fire_effectiveness × DAMAGE_SCALAR / target_protection
  suppression_inflicted = fire_effectiveness × 0.03

Also accounts for protection bonuses from map objects (entrenchments, pillboxes).
"""

from __future__ import annotations

import math
import uuid

from geoalchemy2.shape import to_shape
from shapely.geometry import Point

from backend.engine.terrain import TerrainService
from backend.engine.map_objects import MAP_OBJECT_DEFS

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Base firepower by unit type
BASE_FIREPOWER = {
    "infantry_team": 3,
    "infantry_squad": 6,
    "infantry_section": 7,
    "infantry_platoon": 10,
    "infantry_company": 20,
    "infantry_battalion": 40,
    "mech_platoon": 15,
    "mech_company": 25,
    "tank_platoon": 25,
    "tank_company": 30,
    "artillery_battery": 35,
    "artillery_platoon": 25,
    "mortar_section": 20,
    "mortar_team": 12,
    "at_team": 15,
    "recon_team": 5,
    "recon_section": 7,
    "observation_post": 2,
    "sniper_team": 4,
    "headquarters": 5,
    "command_post": 3,
    "combat_engineer_platoon": 8,
    "combat_engineer_section": 5,
    "combat_engineer_team": 3,
    "mine_layer_section": 3,
    "mine_layer_team": 2,
    "obstacle_breacher_team": 4,
    "obstacle_breacher_section": 6,
    "engineer_recon_team": 3,
    "engineer_platoon": 6,
    "engineer_section": 4,
    "construction_engineer_platoon": 4,
    "construction_engineer_section": 3,
    "avlb_vehicle": 2,
    "avlb_section": 3,
    "logistics_unit": 2,
}

# Default weapon range by unit type (meters)
WEAPON_RANGE = {
    "infantry_team": 300,
    "infantry_squad": 400,
    "infantry_section": 400,
    "infantry_platoon": 600,
    "infantry_company": 800,
    "infantry_battalion": 1200,
    "mech_platoon": 1200,
    "mech_company": 1500,
    "tank_platoon": 2000,
    "tank_company": 2500,
    "artillery_battery": 5000,
    "artillery_platoon": 5000,
    "mortar_section": 3500,
    "mortar_team": 3000,
    "at_team": 2000,
    "recon_team": 400,
    "recon_section": 500,
    "observation_post": 300,
    "sniper_team": 1000,
    "headquarters": 200,
    "command_post": 100,
    "combat_engineer_platoon": 600,
    "combat_engineer_section": 600,
    "combat_engineer_team": 400,
    "mine_layer_section": 300,
    "mine_layer_team": 200,
    "obstacle_breacher_team": 400,
    "obstacle_breacher_section": 500,
    "engineer_recon_team": 400,
    "engineer_platoon": 400,
    "engineer_section": 300,
    "construction_engineer_platoon": 300,
    "construction_engineer_section": 200,
    "avlb_vehicle": 200,
    "avlb_section": 200,
    "logistics_unit": 100,
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


def _get_protection_from_objects(lat: float, lon: float, map_objects: list) -> float:
    """
    Get the best protection bonus from nearby structures/entrenchments.
    Returns multiplier >= 1.0.
    """
    best_protection = 1.0
    unit_point = Point(lon, lat)

    for obj in map_objects:
        if not obj.is_active:
            continue
        if obj.geometry is None:
            continue

        defn = MAP_OBJECT_DEFS.get(obj.object_type)
        if not defn:
            continue

        prot = defn.get("protection_bonus", 1.0)
        if prot <= 1.0:
            continue  # No bonus from this object type

        try:
            obj_shape = to_shape(obj.geometry)
        except Exception:
            continue

        geom_type = obj_shape.geom_type
        in_range = False

        if geom_type in ("Polygon", "MultiPolygon"):
            in_range = obj_shape.contains(unit_point)
        elif geom_type in ("LineString", "MultiLineString"):
            effect_r = defn.get("effect_radius_m", 20)
            buffer_deg = effect_r / 111_320.0
            in_range = obj_shape.buffer(buffer_deg).contains(unit_point)
        elif geom_type == "Point":
            effect_r = defn.get("effect_radius_m", 30)
            dist_dlat = (lat - obj_shape.y) * 111_320.0
            dist_dlon = (lon - obj_shape.x) * 74_000.0
            dist = math.sqrt(dist_dlat * dist_dlat + dist_dlon * dist_dlon)
            in_range = dist <= effect_r

        if in_range and prot > best_protection:
            best_protection = prot

    return best_protection


def process_combat(
    all_units: list,
    terrain: TerrainService,
    map_objects: list | None = None,
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

        # Check for protection bonus from map objects (entrenchments, pillboxes, etc.)
        if map_objects:
            obj_protection = _get_protection_from_objects(tgt_pos[0], tgt_pos[1], map_objects)
            if obj_protection > tgt_protection:
                tgt_protection = obj_protection

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


