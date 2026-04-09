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

# Danger-close radius (meters) — artillery ceases fire if friendlies are this close to target
DANGER_CLOSE_RADIUS_M = 50.0

# Blast radius for area fire (indirect fire at a location, not a specific unit)
AREA_FIRE_BLAST_RADIUS_M = 150.0

# Default number of salvos for fire missions (finite — not infinite)
DEFAULT_FIRE_SALVOS = 3


def _fire_intensity(damage: float) -> str:
    """Map damage value to a natural language fire intensity description."""
    if damage < 0.005:
        return "ineffective fire"
    elif damage < 0.015:
        return "light fire"
    elif damage < 0.035:
        return "moderate fire"
    elif damage < 0.06:
        return "heavy fire"
    else:
        return "devastating fire"


def _strength_category(strength: float) -> str:
    """Map strength value to a natural language description."""
    if strength > 0.85:
        return "at full strength"
    elif strength > 0.65:
        return "lightly damaged"
    elif strength > 0.45:
        return "reduced to ~50%"
    elif strength > 0.25:
        return "heavily damaged"
    else:
        return "near destruction"


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

        # ── Area fire: indirect fire at a location (no specific target unit) ──
        # Artillery/mortar with "fire" task and target_location but no target_unit_id
        if task_type == "fire" and target_location and not target_id:
            if attacker.unit_type in ARTILLERY_TYPES:
                area_evts = _process_area_fire(
                    attacker, atk_pos, target_location, all_units, terrain, map_objects or [],
                )
                events.extend(area_evts)
                # Mark any hit units as under fire
                for evt in area_evts:
                    tid = evt.get("target_unit_id")
                    if tid:
                        under_fire.add(tid)
                # ── Salvo tracking: decrement and complete when done ──
                _decrement_salvos(attacker, events)
                continue  # Area fire processed, skip normal targeting

        target = None
        if target_id:
            for u in all_units:
                if str(u.id) == str(target_id) and not u.is_destroyed:
                    target = u
                    break

        # ── Auto-targeting: find nearest enemy in range if no specific target ──
        if target is None:
            attacker_side = attacker.side.value if hasattr(attacker.side, 'value') else str(attacker.side)
            best_dist = float('inf')
            best_target = None
            weapon_range_search = WEAPON_RANGE.get(attacker.unit_type, 800)
            caps_search = attacker.capabilities or {}
            if caps_search.get("atgm_range_m"):
                weapon_range_search = max(weapon_range_search, caps_search["atgm_range_m"])
            if caps_search.get("mortar_range_m"):
                weapon_range_search = max(weapon_range_search, caps_search["mortar_range_m"])

            for u in all_units:
                if u.is_destroyed:
                    continue
                u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
                if u_side == attacker_side:
                    continue  # same side
                u_pos = _get_position(u)
                if u_pos is None:
                    continue
                d = _distance_m(atk_pos[0], atk_pos[1], u_pos[0], u_pos[1])
                if d <= weapon_range_search and d < best_dist:
                    best_dist = d
                    best_target = u

            if best_target is not None:
                target = best_target
                # Update task with found target for future ticks
                task["target_unit_id"] = str(target.id)
                attacker.current_task = task

        if target is None:
            # No target found — unit will try to advance toward contacts in movement
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
            # Out of range — set target location so movement engine advances unit
            if not task.get("target_location"):
                task["target_location"] = {"lat": tgt_pos[0], "lon": tgt_pos[1]}
                attacker.current_task = task
            continue  # Will move toward target via movement engine

        # ── Danger-close check: artillery/mortar ceases fire if friendly nearby ──
        if attacker.unit_type in ARTILLERY_TYPES:
            attacker_side = attacker.side.value if hasattr(attacker.side, 'value') else str(attacker.side)
            friendly_too_close = False
            for u in all_units:
                if u.is_destroyed or u.id == attacker.id:
                    continue
                u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
                if u_side != attacker_side:
                    continue
                u_pos = _get_position(u)
                if u_pos is None:
                    continue
                d_to_target = _distance_m(u_pos[0], u_pos[1], tgt_pos[0], tgt_pos[1])
                if d_to_target <= DANGER_CLOSE_RADIUS_M:
                    friendly_too_close = True
                    break
            if friendly_too_close:
                attacker.current_task = None
                events.append({
                    "event_type": "ceasefire_friendly",
                    "actor_unit_id": attacker.id,
                    "target_unit_id": target.id,
                    "text_summary": f"{attacker.name} ceasing fire — friendly forces within danger close range of target",
                    "payload": {
                        "attacker": str(attacker.id),
                        "target": str(target.id),
                        "reason": "danger_close",
                    },
                })
                continue

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
        dig_in_level = tgt_task.get("dig_in_level", 0)
        if tgt_task.get("type") in ("defend", "dig_in"):
            # Graduated dig-in protection: base terrain + 0.2 per level, capped at 2.5
            tgt_protection = min(2.5, tgt_terrain * (1.0 + 0.2 * dig_in_level))
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
            # Collect all attackers that were engaging this target
            involved_ids = [str(attacker.id)]
            for u in all_units:
                if u.is_destroyed or u.id == attacker.id:
                    continue
                u_task = u.current_task
                if u_task and u_task.get("target_unit_id") == str(target.id):
                    involved_ids.append(str(u.id))
                    # Clear the engage task — target is gone
                    u.current_task = None
            # Clear attacker's task too
            attacker.current_task = None
            events.append({
                "event_type": "unit_destroyed",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": f"{attacker.name} destroyed {target.name}",
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "involved_unit_ids": involved_ids,
                    "target_lat": tgt_pos[0],
                    "target_lon": tgt_pos[1],
                },
            })
        else:
            # Natural language combat description
            fire_desc = _fire_intensity(damage)
            strength_desc = _strength_category(target.strength)
            is_arty = attacker.unit_type in ARTILLERY_TYPES
            events.append({
                "event_type": "combat",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": (
                    f"{attacker.name} engaging {target.name} — "
                    f"{fire_desc}, target {strength_desc}"
                ),
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "damage": round(damage, 4),
                    "suppression": round(suppression_inflicted, 4),
                    "target_strength": round(target.strength, 4),
                    "distance_m": round(dist, 1),
                    "target_lat": tgt_pos[0],
                    "target_lon": tgt_pos[1],
                    "is_artillery": is_arty,
                },
            })

        # ── Salvo tracking for artillery targeted fire ──
        if attacker.unit_type in ARTILLERY_TYPES and attacker.current_task:
            _decrement_salvos(attacker, events)

    return events, under_fire


def _decrement_salvos(unit, events: list[dict]) -> None:
    """
    Track salvo count for artillery fire tasks.

    Each tick of firing decrements salvos_remaining by 1.
    When it reaches 0, the fire task is cleared (mission complete).
    Default salvos: DEFAULT_FIRE_SALVOS (3).
    """
    task = unit.current_task
    if not task:
        return

    # Initialize salvos_remaining if not set
    salvos = task.get("salvos_remaining")
    if salvos is None:
        salvos = task.get("salvos", DEFAULT_FIRE_SALVOS)
        task["salvos_remaining"] = salvos

    salvos -= 1
    task["salvos_remaining"] = salvos
    # Force SQLAlchemy JSONB change detection by assigning a new dict
    unit.current_task = dict(task)

    if salvos <= 0:
        unit.current_task = None
        events.append({
            "event_type": "order_completed",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} fire mission complete — salvos expended",
            "payload": {
                "unit_id": str(unit.id),
                "reason": "salvos_expended",
            },
        })


def _process_area_fire(
    attacker,
    atk_pos: tuple[float, float],
    target_location: dict,
    all_units: list,
    terrain: TerrainService,
    map_objects: list,
) -> list[dict]:
    """
    Process indirect area fire at a location (no specific target unit).

    Artillery/mortar fires at a grid square — deals damage to any enemy units
    within the blast radius of the target location. Also generates impact
    visual effects even if no enemy is hit.

    Returns list of event dicts.
    """
    events = []
    target_lat = target_location.get("lat")
    target_lon = target_location.get("lon")
    if target_lat is None or target_lon is None:
        return events

    # Check range
    dist_to_target = _distance_m(atk_pos[0], atk_pos[1], target_lat, target_lon)
    weapon_range = WEAPON_RANGE.get(attacker.unit_type, 5000)
    caps = attacker.capabilities or {}
    if caps.get("mortar_range_m"):
        weapon_range = max(weapon_range, caps["mortar_range_m"])

    if dist_to_target > weapon_range:
        # Out of range — generate event but don't fire
        events.append({
            "event_type": "fire_out_of_range",
            "actor_unit_id": attacker.id,
            "text_summary": f"{attacker.name} — target out of range ({dist_to_target:.0f}m, max {weapon_range}m)",
            "payload": {
                "attacker": str(attacker.id),
                "distance_m": round(dist_to_target, 1),
                "weapon_range_m": weapon_range,
            },
        })
        return events

    # Check ammo
    ammo = attacker.ammo or 1.0
    if ammo <= 0:
        return events

    # Danger-close check: don't fire if friendly forces within danger close radius of target
    attacker_side = attacker.side.value if hasattr(attacker.side, 'value') else str(attacker.side)
    for u in all_units:
        if u.is_destroyed or u.id == attacker.id:
            continue
        u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
        if u_side != attacker_side:
            continue
        u_pos = _get_position(u)
        if u_pos is None:
            continue
        d_friendly = _distance_m(u_pos[0], u_pos[1], target_lat, target_lon)
        if d_friendly <= DANGER_CLOSE_RADIUS_M:
            attacker.current_task = None
            events.append({
                "event_type": "ceasefire_friendly",
                "actor_unit_id": attacker.id,
                "text_summary": f"{attacker.name} ceasing fire — friendly forces within danger close range of target",
                "payload": {
                    "attacker": str(attacker.id),
                    "reason": "danger_close",
                    "target_lat": target_lat,
                    "target_lon": target_lon,
                },
            })
            return events

    # Calculate fire effectiveness
    base_fp = BASE_FIREPOWER.get(attacker.unit_type, 20)
    strength = attacker.strength or 1.0
    suppression = attacker.suppression or 0.0

    fire_effectiveness = (
        base_fp
        * strength
        * _ammo_factor(ammo)
        * (1.0 - suppression)
    )

    # Find enemy units within blast radius of the target location
    hit_any = False
    for target in all_units:
        if target.is_destroyed:
            continue
        t_side = target.side.value if hasattr(target.side, 'value') else str(target.side)
        if t_side == attacker_side:
            continue  # Same side — don't hit friendlies

        tgt_pos = _get_position(target)
        if tgt_pos is None:
            continue

        dist_to_blast = _distance_m(tgt_pos[0], tgt_pos[1], target_lat, target_lon)
        if dist_to_blast > AREA_FIRE_BLAST_RADIUS_M:
            continue

        # Damage falls off with distance from blast center
        proximity_factor = max(0.2, 1.0 - (dist_to_blast / AREA_FIRE_BLAST_RADIUS_M))

        # Target protection
        tgt_terrain = terrain.protection_factor(tgt_pos[1], tgt_pos[0])
        tgt_task = target.current_task or {}
        dig_in_level = tgt_task.get("dig_in_level", 0)
        if tgt_task.get("type") in ("defend", "dig_in"):
            tgt_protection = min(2.5, tgt_terrain * (1.0 + 0.2 * dig_in_level))
        else:
            tgt_protection = tgt_terrain

        # Protection from map objects
        if map_objects:
            obj_protection = _get_protection_from_objects(tgt_pos[0], tgt_pos[1], map_objects)
            if obj_protection > tgt_protection:
                tgt_protection = obj_protection

        # Apply area damage (reduced by proximity and protection)
        damage = fire_effectiveness * DAMAGE_SCALAR * proximity_factor / tgt_protection
        target.strength = max(0.0, (target.strength or 1.0) - damage)

        # Apply suppression (area fire is very suppressive)
        suppression_inflicted = fire_effectiveness * 0.04 * proximity_factor
        target.suppression = min(1.0, (target.suppression or 0.0) + suppression_inflicted)

        hit_any = True

        if target.strength <= 0.01:
            target.is_destroyed = True
            target.current_task = None
            events.append({
                "event_type": "unit_destroyed",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": f"{attacker.name} destroyed {target.name} with indirect fire",
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "target_lat": tgt_pos[0],
                    "target_lon": tgt_pos[1],
                    "is_artillery": True,
                },
            })
        else:
            fire_desc = _fire_intensity(damage)
            strength_desc = _strength_category(target.strength)
            events.append({
                "event_type": "combat",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": (
                    f"{attacker.name} area fire on {target.name} — "
                    f"{fire_desc}, target {strength_desc}"
                ),
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "damage": round(damage, 4),
                    "suppression": round(suppression_inflicted, 4),
                    "target_strength": round(target.strength, 4),
                    "distance_m": round(dist_to_blast, 1),
                    "target_lat": target_lat,
                    "target_lon": target_lon,
                    "is_artillery": True,
                    "area_fire": True,
                },
            })

    # Always generate an impact event for visual effects,
    # even if no enemy was directly hit
    events.append({
        "event_type": "combat",
        "actor_unit_id": attacker.id,
        "text_summary": (
            f"{attacker.name} firing at grid location"
            + (f" — {len([e for e in events if e.get('target_unit_id')])} enemies in blast zone" if hit_any else " — area suppression")
        ),
        "payload": {
            "attacker": str(attacker.id),
            "target_lat": target_lat,
            "target_lon": target_lon,
            "is_artillery": True,
            "area_fire": True,
            "hit_enemies": hit_any,
        },
    })

    return events


# ── Artillery unit types that can provide fire support ──
ARTILLERY_TYPES = {
    "artillery_battery", "artillery_platoon",
    "mortar_section", "mortar_team",
}


def process_artillery_support(
    all_units: list,
    terrain: TerrainService | None = None,
) -> list[dict]:
    """
    Auto-assign idle artillery units to support attacking units in their CoC.

    Walks up each attacking unit's parent chain to find artillery siblings
    (children of the same parent). If found and idle with ammo, assigns
    a fire task targeting the attacker's target.

    Returns list of event dicts.
    """
    events = []

    # Build lookup maps
    units_by_id = {str(u.id): u for u in all_units if not u.is_destroyed}
    children_by_parent = {}  # parent_id → [unit, ...]
    for u in all_units:
        if u.is_destroyed:
            continue
        pid = str(u.parent_unit_id) if u.parent_unit_id else None
        if pid:
            children_by_parent.setdefault(pid, []).append(u)

    # Track which artillery units have already been tasked this tick
    tasked_artillery = set()

    for unit in all_units:
        if unit.is_destroyed:
            continue
        task = unit.current_task
        if not task:
            continue
        task_type = task.get("type", "")
        if task_type not in ("attack", "engage", "fire"):
            continue

        # Get the target location
        target_loc = task.get("target_location")
        target_uid = task.get("target_unit_id")
        if not target_loc and target_uid:
            # Resolve from the target unit's position
            tgt = units_by_id.get(str(target_uid))
            if tgt:
                tgt_pos = _get_position(tgt)
                if tgt_pos:
                    target_loc = {"lat": tgt_pos[0], "lon": tgt_pos[1]}
        if not target_loc:
            continue

        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

        # Walk up the CoC to find artillery siblings (up to 3 levels)
        visited = set()
        current_id = str(unit.id)
        for _ in range(3):
            current = units_by_id.get(current_id)
            if not current or not current.parent_unit_id:
                break
            parent_id = str(current.parent_unit_id)
            if parent_id in visited:
                break
            visited.add(parent_id)

            # Check all siblings (children of the same parent)
            siblings = children_by_parent.get(parent_id, [])
            for sib in siblings:
                if str(sib.id) in tasked_artillery:
                    continue
                if sib.unit_type not in ARTILLERY_TYPES:
                    continue
                sib_side = sib.side.value if hasattr(sib.side, 'value') else str(sib.side)
                if sib_side != unit_side:
                    continue
                if sib.is_destroyed:
                    continue
                if (sib.ammo or 0) <= 0:
                    continue

                # Check if artillery already has a task
                sib_task = sib.current_task
                if sib_task and sib_task.get("type") in ("fire", "attack", "engage"):
                    continue  # Already firing

                # Check range
                sib_pos = _get_position(sib)
                if not sib_pos:
                    continue
                weapon_range = WEAPON_RANGE.get(sib.unit_type, 5000)
                dist = _distance_m(sib_pos[0], sib_pos[1], target_loc["lat"], target_loc["lon"])
                if dist > weapon_range:
                    continue

                # Danger-close check: don't assign if friendly within 50m of target
                friendly_danger = False
                for fu in all_units:
                    if fu.is_destroyed or fu.id == sib.id:
                        continue
                    fu_side = fu.side.value if hasattr(fu.side, 'value') else str(fu.side)
                    if fu_side != unit_side:
                        continue
                    fu_pos = _get_position(fu)
                    if fu_pos is None:
                        continue
                    d_friendly = _distance_m(fu_pos[0], fu_pos[1], target_loc["lat"], target_loc["lon"])
                    if d_friendly <= DANGER_CLOSE_RADIUS_M:
                        friendly_danger = True
                        break
                if friendly_danger:
                    continue

                # Assign fire mission
                sib.current_task = {
                    "type": "fire",
                    "target_location": target_loc,
                    "target_unit_id": target_uid,
                    "support_for": str(unit.id),
                    "salvos_remaining": DEFAULT_FIRE_SALVOS,
                }
                tasked_artillery.add(str(sib.id))

                events.append({
                    "event_type": "artillery_support",
                    "actor_unit_id": sib.id,
                    "target_unit_id": uuid.UUID(target_uid) if target_uid else None,
                    "text_summary": f"{sib.name} firing in support of {unit.name}",
                    "payload": {
                        "artillery_id": str(sib.id),
                        "supported_unit_id": str(unit.id),
                        "target_lat": target_loc["lat"],
                        "target_lon": target_loc["lon"],
                    },
                })
                break  # One artillery unit per requesting unit per tick

            current_id = parent_id

    return events
