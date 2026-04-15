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

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point

from backend.engine.terrain import TerrainService
from backend.engine.map_objects import MAP_OBJECT_DEFS
from backend.models.map_object import MapObject, ObjectCategory, ObjectSide

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
    contacts: list | None = None,
    new_map_objects_out: list | None = None,
) -> tuple[list[dict], set[uuid.UUID]]:
    """
    Resolve combat for all units with attack/engage tasks.

    Args:
        contacts: List of Contact ORM objects. Used to restrict auto-targeting
                  to only enemies that have been detected (fog-of-war compliant).

    Returns list of event dicts.
    """
    from backend.services.debug_logger import dlog, is_debug_logging_enabled
    _debug = is_debug_logging_enabled()

    events = []
    # Track which units are under fire this tick (for suppression recovery)
    under_fire: set[uuid.UUID] = set()

    # Build set of detected enemy unit IDs per side (for FOW-safe auto-targeting)
    _detected_by_side: dict[str, set[str]] = {"blue": set(), "red": set()}
    if contacts:
        for c in contacts:
            if c.target_unit_id and not c.is_stale:
                obs_side = c.observing_side.value if hasattr(c.observing_side, 'value') else str(c.observing_side)
                _detected_by_side.setdefault(obs_side, set()).add(str(c.target_unit_id))

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

        if _debug and task_type == "fire":
            dlog(f"    [combat] {attacker.name} ({attacker.unit_type}) task=fire target_id={target_id} target_loc={target_location} is_arty={attacker.unit_type in ARTILLERY_TYPES}")

        # ── Area fire: indirect fire at a location (no specific target unit) ──
        # Artillery/mortar with "fire" task and target_location but no target_unit_id
        if task_type == "fire" and target_location and not target_id:
            if attacker.unit_type in ARTILLERY_TYPES:
                area_evts = _process_area_fire(
                    attacker,
                    atk_pos,
                    target_location,
                    all_units,
                    terrain,
                    map_objects or [],
                    task=task,
                    new_map_objects_out=new_map_objects_out,
                )
                events.extend(area_evts)
                # Mark any hit units as under fire
                for evt in area_evts:
                    tid = evt.get("target_unit_id")
                    if tid:
                        under_fire.add(tid)
                # ── Salvo tracking: decrement and complete when done ──
                _decrement_salvos(attacker, events, all_units)
                continue  # Area fire processed, skip normal targeting

        target = None
        if target_id:
            for u in all_units:
                if str(u.id) == str(target_id) and not u.is_destroyed:
                    target = u
                    break
            if _debug and target is None:
                dlog(f"    [combat] {attacker.name}: target_unit_id={target_id} NOT FOUND in all_units (destroyed or missing)")

        # ── Auto-targeting: find nearest DETECTED enemy in range if no specific target ──
        # Only targets enemies that the attacker's side has contacts for (fog-of-war safe).
        if target is None:
            attacker_side = attacker.side.value if hasattr(attacker.side, 'value') else str(attacker.side)
            detected_enemies = _detected_by_side.get(attacker_side, set())
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
                # FOW CHECK: only auto-target enemies that have been detected
                if str(u.id) not in detected_enemies:
                    continue
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
                task = dict(task)  # new dict for SQLAlchemy JSONB change detection
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
            # Out of range — set/update target location so movement engine advances unit
            # ALWAYS update to the enemy's CURRENT position (even if target_location
            # was set before) — if the enemy moved, we must track the new position.
            if _debug:
                dlog(f"    [combat] {attacker.name}: target {target.name} OUT OF RANGE dist={dist:.0f}m > range={weapon_range}m")
            task = dict(task)  # new dict for SQLAlchemy JSONB change detection
            task["target_location"] = {"lat": tgt_pos[0], "lon": tgt_pos[1]}
            attacker.current_task = task
            continue  # Will move toward target via movement engine

        # ── In range: keep target_location updated to enemy's current position ──
        # This ensures that if the target moves out of range on a future tick,
        # the attacker will pursue the correct (updated) position.
        old_loc = task.get("target_location")
        if old_loc:
            old_lat = old_loc.get("lat", 0)
            old_lon = old_loc.get("lon", 0)
            # Update if the target has moved significantly (>50m) from the stored location
            if _distance_m(old_lat, old_lon, tgt_pos[0], tgt_pos[1]) > 50:
                task = dict(task)
                task["target_location"] = {"lat": tgt_pos[0], "lon": tgt_pos[1]}
                attacker.current_task = task

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

        # ── Combat role modifiers ──
        combat_role = task.get("combat_role")
        if combat_role == "suppress":
            # Suppressing fire: reduced damage but increased suppression
            fire_effectiveness *= 0.6
        elif combat_role == "flank":
            # Flanking: slightly reduced effectiveness while maneuvering
            fire_effectiveness *= 0.85

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
        suppression_rate = 0.03
        if combat_role == "suppress":
            suppression_rate = 0.045  # Suppressing units generate 50% more suppression
        suppression_inflicted = fire_effectiveness * suppression_rate
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
            _decrement_salvos(attacker, events, all_units)

    return events, under_fire


def _resolve_supported_fire_target(
    support_task: dict,
    supported_unit,
    units_by_id: dict[str, object],
) -> tuple[dict | None, str | None]:
    """Resolve the best current fire target for a sustained support relationship."""
    if supported_unit is None or supported_unit.is_destroyed:
        return None, None

    supported_task = supported_unit.current_task or {}
    if not supported_task or supported_task.get("awaiting_ceasefire"):
        return None, None

    target_uid = (
        supported_task.get("request_fire_target_unit_id")
        or supported_task.get("target_unit_id")
        or support_task.get("target_unit_id")
    )
    if target_uid:
        target = units_by_id.get(str(target_uid))
        if target is not None and not target.is_destroyed:
            tgt_pos = _get_position(target)
            if tgt_pos is not None:
                return {"lat": tgt_pos[0], "lon": tgt_pos[1]}, str(target.id)
        return None, None

    target_loc = (
        supported_task.get("request_fire_target_location")
        or supported_task.get("target_location")
        or support_task.get("target_location")
    )
    if target_loc and target_loc.get("lat") is not None and target_loc.get("lon") is not None:
        return {
            "lat": float(target_loc["lat"]),
            "lon": float(target_loc["lon"]),
        }, None

    return None, None


def _decrement_salvos(unit, events: list[dict], all_units: list | None = None) -> None:
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
        if task.get("sustained_support") and not task.get("ceasefire_requested_by") and all_units:
            units_by_id = {str(u.id): u for u in all_units if not u.is_destroyed}
            supported_unit = units_by_id.get(str(task.get("support_for")))
            next_target_loc, next_target_uid = _resolve_supported_fire_target(
                task,
                supported_unit,
                units_by_id,
            )
            if next_target_loc:
                refreshed_task = dict(task)
                refreshed_task["salvos_remaining"] = 1
                refreshed_task["target_location"] = next_target_loc
                if next_target_uid:
                    refreshed_task["target_unit_id"] = next_target_uid
                elif refreshed_task.get("target_unit_id"):
                    refreshed_task.pop("target_unit_id", None)
                unit.current_task = refreshed_task
                return

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
    task: dict | None = None,
    new_map_objects_out: list | None = None,
) -> list[dict]:
    """
    Process indirect area fire at a location (no specific target unit).

    Artillery/mortar fires at a grid square — deals damage to any enemy units
    within the blast radius of the target location. Also generates impact
    visual effects even if no enemy is hit.

    Returns list of event dicts.
    """
    from backend.services.debug_logger import dlog, is_debug_logging_enabled
    _debug = is_debug_logging_enabled()

    events = []
    target_lat = target_location.get("lat")
    target_lon = target_location.get("lon")
    if target_lat is None or target_lon is None:
        if _debug:
            dlog(f"    [area_fire] {attacker.name}: NO target lat/lon in target_location={target_location}")
        return events

    # Check range
    dist_to_target = _distance_m(atk_pos[0], atk_pos[1], target_lat, target_lon)
    weapon_range = WEAPON_RANGE.get(attacker.unit_type, 5000)
    caps = attacker.capabilities or {}
    if caps.get("mortar_range_m"):
        weapon_range = max(weapon_range, caps["mortar_range_m"])

    if dist_to_target > weapon_range:
        # Out of range — generate event but don't fire
        if _debug:
            dlog(f"    [area_fire] {attacker.name}: OUT OF RANGE dist={dist_to_target:.0f}m > range={weapon_range}m")
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

    fire_effect_type = (task or {}).get("fire_effect_type") or (task or {}).get("map_object_type")
    if fire_effect_type == "smoke":
        smoke_radius_m = float((task or {}).get("smoke_radius_m") or target_location.get("radius_m") or 100.0)
        smoke_duration_ticks = int((task or {}).get("smoke_duration_ticks") or target_location.get("duration_ticks") or 3)
        smoke_poly = Point(target_lon, target_lat).buffer(smoke_radius_m / 111_320.0, resolution=16)
        smoke_obj = MapObject(
            session_id=attacker.session_id,
            side=ObjectSide.neutral,
            object_type="smoke",
            object_category=ObjectCategory.effect,
            geometry=from_shape(smoke_poly, srid=4326),
            properties={
                "ticks_remaining": smoke_duration_ticks,
                "radius_m": smoke_radius_m,
                "fired_by": str(attacker.id),
            },
            label=f"Smoke ({attacker.name})",
            is_active=True,
            discovered_by_blue=True,
            discovered_by_red=True,
        )
        if new_map_objects_out is not None:
            new_map_objects_out.append(smoke_obj)
        events.append({
            "event_type": "smoke_deployed",
            "actor_unit_id": attacker.id,
            "text_summary": f"{attacker.name} deployed smoke at target location",
            "payload": {
                "attacker": str(attacker.id),
                "target_lat": target_lat,
                "target_lon": target_lon,
                "radius_m": smoke_radius_m,
                "duration_ticks": smoke_duration_ticks,
            },
        })
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
    if _debug:
        dlog(f"    [area_fire] {attacker.name}: FIRING at ({target_lat:.4f},{target_lon:.4f}) dist={dist_to_target:.0f}m FE={fire_effectiveness:.1f} ammo={ammo:.2f}")
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
            # Clear all other units' engage tasks targeting this destroyed unit
            involved_ids = [str(attacker.id)]
            for u in all_units:
                if u.is_destroyed or u.id == attacker.id:
                    continue
                u_task = u.current_task
                if u_task and u_task.get("target_unit_id") == str(target.id):
                    involved_ids.append(str(u.id))
                    u.current_task = None
            events.append({
                "event_type": "unit_destroyed",
                "actor_unit_id": attacker.id,
                "target_unit_id": target.id,
                "text_summary": f"{attacker.name} destroyed {target.name} with indirect fire",
                "payload": {
                    "attacker": str(attacker.id),
                    "target": str(target.id),
                    "involved_unit_ids": involved_ids,
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

# ── Combat coordination: cease-fire request radius ──
# When friendly infantry is within this distance of an artillery target,
# the infantry halts and requests cease-fire before storming.
CEASEFIRE_REQUEST_RADIUS_M = 250.0

# ── Combat role assignment ──
# Unit types best suited for suppression (support weapons, lower mobility)
SUPPRESSION_PREFERRED_TYPES = {
    "mortar_section", "mortar_team", "at_team", "mech_platoon",
    "mech_company", "tank_platoon", "tank_company",
    "observation_post", "sniper_team",
}

# Minimum weapon range fraction at which suppressing units hold position
SUPPRESS_HOLD_RANGE_FRACTION = 0.80

# Re-evaluate combat roles every N ticks to prevent oscillation
COMBAT_ROLE_REASSIGN_INTERVAL = 3


def assign_combat_roles(
    all_units: list,
    terrain: TerrainService,
    assignment_tick: int = 0,
) -> list[dict]:
    """
    Assign combat roles (suppress/assault/flank) to groups of units
    attacking the same target. This creates tactical coordination so
    units don't all charge to point-blank range.

    Rules:
    - Groups of 1 unit: no role assignment needed (they do everything).
    - Groups of 2+:
        - ~40% assigned "suppress" (stay at range, provide covering fire)
        - 1-2 assigned "assault" (close in for the kill — strongest infantry)
        - remainder assigned "flank" (approach from an offset angle)
    - Roles stored in unit.current_task["combat_role"]
    - Role reassignment throttled to every COMBAT_ROLE_REASSIGN_INTERVAL ticks.

    Returns list of radio chatter event dicts.
    """
    events = []

    # Group attackers by target_unit_id
    target_groups: dict[str, list] = {}  # target_id → [attacking_units]
    for u in all_units:
        if u.is_destroyed:
            continue
        task = u.current_task
        if not task:
            continue
        task_type = task.get("type", "")
        if task_type not in ("attack", "engage"):
            continue
        # Skip artillery — they have their own fire missions
        if u.unit_type in ARTILLERY_TYPES:
            continue
        target_id = task.get("target_unit_id")
        if target_id:
            target_groups.setdefault(str(target_id), []).append(u)

    # Build target lookup
    units_by_id = {str(u.id): u for u in all_units if not u.is_destroyed}

    for target_id, attackers in target_groups.items():
        if len(attackers) < 2:
            # Solo attacker — no coordination needed, clear any stale role
            if (
                attackers[0].current_task
                and attackers[0].current_task.get("combat_role")
                and not attackers[0].current_task.get("combat_role_locked")
            ):
                task = dict(attackers[0].current_task)
                del task["combat_role"]
                attackers[0].current_task = task
            continue

        target = units_by_id.get(target_id)
        if not target:
            continue

        tgt_pos = _get_position(target)
        if not tgt_pos:
            continue

        # Check if roles were recently assigned — throttle reassignment
        any_has_role = any(
            (u.current_task or {}).get("combat_role") for u in attackers
        )
        if any_has_role:
            # Check tick of last assignment
            assign_tick = max(
                (u.current_task or {}).get("combat_role_assigned_tick", 0)
                for u in attackers
            )
            # If all attackers have roles and it's recent, skip
            all_have_role = all(
                (u.current_task or {}).get("combat_role") for u in attackers
            )
            if all_have_role and assign_tick > 0:
                continue  # roles are still valid, don't reassign yet

        # Sort attackers: those with long range / heavy weapons suppress,
        # strongest infantry assaults
        locked_units: list[tuple] = []
        attacker_info = []
        for u in attackers:
            u_pos = _get_position(u)
            if not u_pos:
                continue
            dist = _distance_m(u_pos[0], u_pos[1], tgt_pos[0], tgt_pos[1])
            w_range = WEAPON_RANGE.get(u.unit_type, 800)
            fp = BASE_FIREPOWER.get(u.unit_type, 5)
            info = {
                "unit": u,
                "dist": dist,
                "weapon_range": w_range,
                "firepower": fp,
                "is_suppress_type": u.unit_type in SUPPRESSION_PREFERRED_TYPES,
            }
            if (u.current_task or {}).get("combat_role_locked"):
                locked_units.append((u, dist, w_range))
            else:
                attacker_info.append(info)

        if len(attacker_info) == 0:
            continue

        # Determine roles:
        # 1. Units already at good range with heavy weapons → suppress
        # 2. Strongest / closest infantry-type → assault (max 1-2)
        # 3. Rest → flank (approach from offset angle)

        n_suppress = max(1, len(attacker_info) * 2 // 5)  # ~40%
        n_assault = min(2, max(1, len(attacker_info) - n_suppress))
        n_flank = len(attacker_info) - n_suppress - n_assault

        # Score for suppression: prefer units at range + suppression types + high firepower
        for info in attacker_info:
            range_score = min(1.0, info["dist"] / info["weapon_range"]) if info["weapon_range"] > 0 else 0
            info["suppress_score"] = (
                range_score * 0.4
                + (1.0 if info["is_suppress_type"] else 0.0) * 0.4
                + min(1.0, info["firepower"] / 30.0) * 0.2
            )

        # Score for assault: prefer close units with medium firepower (infantry)
        for info in attacker_info:
            closeness = max(0, 1.0 - info["dist"] / 2000.0)
            is_infantry = not info["is_suppress_type"]
            info["assault_score"] = (
                closeness * 0.5
                + (1.0 if is_infantry else 0.3) * 0.3
                + min(1.0, (info["unit"].strength or 1.0)) * 0.2
            )

        # Assign suppressors first (highest suppress_score)
        sorted_suppress = sorted(attacker_info, key=lambda x: x["suppress_score"], reverse=True)
        suppressors = sorted_suppress[:n_suppress]
        remaining = sorted_suppress[n_suppress:]

        # From remaining, assign assaulters (highest assault_score)
        sorted_assault = sorted(remaining, key=lambda x: x["assault_score"], reverse=True)
        assaulters = sorted_assault[:n_assault]
        flankers = sorted_assault[n_assault:]

        for u, dist, weapon_range in locked_units:
            locked_role = (u.current_task or {}).get("combat_role") or (
                "flank" if (u.current_task or {}).get("maneuver_kind") == "flank" else None
            )
            if locked_role:
                _apply_combat_role(
                    u, locked_role, dist, weapon_range, tgt_pos, terrain,
                    assignment_tick=assignment_tick,
                )
        # Apply roles
        for info in suppressors:
            _apply_combat_role(
                info["unit"], "suppress", info["dist"], info["weapon_range"], tgt_pos,
                assignment_tick=assignment_tick,
            )
        for info in assaulters:
            _apply_combat_role(
                info["unit"], "assault", info["dist"], info["weapon_range"], tgt_pos,
                assignment_tick=assignment_tick,
            )
        for info in flankers:
            _apply_combat_role(
                info["unit"], "flank", info["dist"], info["weapon_range"], tgt_pos, terrain,
                assignment_tick=assignment_tick,
            )

    return events


def _apply_combat_role(unit, role: str, dist: float, weapon_range: float,
                       tgt_pos: tuple, terrain: TerrainService | None = None,
                       assignment_tick: int = 0):
    """Apply a combat role to a unit's current task."""
    task = dict(unit.current_task) if unit.current_task else {"type": "engage"}
    task["combat_role"] = role
    task["combat_role_assigned_tick"] = assignment_tick

    if role == "suppress":
        # Suppressing units hold at ~80% of their weapon range
        hold_dist = weapon_range * SUPPRESS_HOLD_RANGE_FRACTION
        if dist <= hold_dist:
            # Already within suppression range — remove target_location to stop advancing
            task.pop("target_location", None)

    elif role == "flank":
        # Flanking units get their target_location offset 60° from direct line
        if tgt_pos and unit.position is not None:
            try:
                pt = to_shape(unit.position)
                cur_lat, cur_lon = pt.y, pt.x
                # Compute bearing from unit to target
                dy = (tgt_pos[0] - cur_lat) * METERS_PER_DEG_LAT
                dx = (tgt_pos[1] - cur_lon) * METERS_PER_DEG_LON_AT_48
                bearing_rad = math.atan2(dx, dy)
                # Offset by 60° (try both sides, pick one with better protection)
                offset_rad = math.radians(60)
                flank_dist_m = max(200, min(dist * 0.7, 500))
                best_pos = None
                best_prot = 0
                side_pref = task.get("maneuver_side")
                if side_pref == "left":
                    signs = (-1,)
                elif side_pref == "right":
                    signs = (1,)
                else:
                    signs = (1, -1)
                chosen_sign = None
                for sign in signs:
                    flank_bearing = bearing_rad + sign * offset_rad
                    flank_lat = tgt_pos[0] + flank_dist_m * math.cos(flank_bearing) / METERS_PER_DEG_LAT
                    flank_lon = tgt_pos[1] + flank_dist_m * math.sin(flank_bearing) / METERS_PER_DEG_LON_AT_48
                    prot = 0
                    if terrain:
                        prot = terrain.protection_factor(flank_lon, flank_lat)
                    if best_pos is None or prot > best_prot:
                        best_prot = prot
                        best_pos = (flank_lat, flank_lon)
                        chosen_sign = sign
                if best_pos:
                    task["target_location"] = {"lat": best_pos[0], "lon": best_pos[1]}
                    task["flank_assault_location"] = {"lat": tgt_pos[0], "lon": tgt_pos[1]}
                    task["flank_phase"] = "approach"
                    task["maneuver_kind"] = "flank"
                    if not task.get("maneuver_side"):
                        task["maneuver_side"] = "left" if chosen_sign == -1 else "right"
            except Exception:
                pass

    unit.current_task = task


def check_artillery_ceasefire_coordination(
    all_units: list,
    terrain: TerrainService,
) -> list[dict]:
    """
    Check if assault units approaching a bombarded target need to request
    cease-fire from friendly artillery before storming.

    When a non-artillery unit with assault role (or engage/attack task) is within
    CEASEFIRE_REQUEST_RADIUS_M of a location being bombarded by friendly artillery,
    the infantry halts and the artillery finishes its current salvo then ceases fire.

    Also handles the resume: units with awaiting_ceasefire flag resume when
    no friendly artillery is still firing at that area.

    Returns list of event dicts (radio chatter).
    """
    events = []

    # Build maps of artillery firing positions
    arty_targets: dict[str, list] = {}  # serialized target area → [artillery units]
    for u in all_units:
        if u.is_destroyed:
            continue
        if u.unit_type not in ARTILLERY_TYPES:
            continue
        task = u.current_task
        if not task:
            continue
        if task.get("type") != "fire":
            continue
        target_loc = task.get("target_location")
        if not target_loc:
            continue
        # Key by approximate area (round to ~50m grid)
        key = f"{round(target_loc['lat'], 4)}_{round(target_loc['lon'], 4)}"
        arty_targets.setdefault(key, []).append(u)

    for u in all_units:
        if u.is_destroyed:
            continue
        if u.unit_type in ARTILLERY_TYPES:
            continue

        task = u.current_task
        if not task:
            continue

        task_type = task.get("type", "")

        # ── Resume check: units waiting for cease-fire ──
        if task.get("awaiting_ceasefire"):
            ceasefire_target = task.get("ceasefire_target")
            if ceasefire_target:
                key = f"{round(ceasefire_target['lat'], 4)}_{round(ceasefire_target['lon'], 4)}"
                arty_still_firing = key in arty_targets
                if not arty_still_firing:
                    # Artillery has ceased — resume advance
                    new_task = dict(task)
                    del new_task["awaiting_ceasefire"]
                    del new_task["ceasefire_target"]
                    u.current_task = new_task
                    side = u.side.value if hasattr(u.side, 'value') else str(u.side)
                    events.append({
                        "event_type": "ceasefire_cleared",
                        "actor_unit_id": u.id,
                        "text_summary": f"{u.name} — artillery cease-fire confirmed, resuming advance",
                        "payload": {
                            "unit_id": str(u.id),
                            "reason": "ceasefire_cleared",
                        },
                    })
            continue  # Don't process further while waiting

        # ── Check if this unit is approaching a bombarded area ──
        if task_type not in ("attack", "engage", "advance"):
            continue

        u_pos = _get_position(u)
        if not u_pos:
            continue

        u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)

        # Check all friendly artillery targets
        for key, arty_units in arty_targets.items():
            # Check if any arty is same side
            same_side_arty = [
                a for a in arty_units
                if (a.side.value if hasattr(a.side, 'value') else str(a.side)) == u_side
            ]
            linked_ids = {str(uid) for uid in (task.get("supporting_unit_ids") or []) if uid}
            if linked_ids:
                linked_same_side = [a for a in same_side_arty if str(a.id) in linked_ids]
                if linked_same_side:
                    same_side_arty = linked_same_side
            if not same_side_arty:
                continue

            arty_task = same_side_arty[0].current_task
            if not arty_task:
                continue
            arty_target = arty_task.get("target_location")
            if not arty_target:
                continue

            # Is this unit close enough to the bombardment area?
            dist_to_bombardment = _distance_m(
                u_pos[0], u_pos[1],
                arty_target["lat"], arty_target["lon"]
            )

            progress_stage = task.get("fire_support_progress_stage", 0)
            if 250.0 < dist_to_bombardment <= 700.0:
                desired_stage = 2 if dist_to_bombardment <= 400.0 else 1
                if desired_stage > progress_stage:
                    new_task = dict(task)
                    new_task["fire_support_progress_stage"] = desired_stage
                    u.current_task = new_task
                    events.append({
                        "event_type": "fire_support_progress",
                        "actor_unit_id": u.id,
                        "text_summary": (
                            f"{u.name} updating {same_side_arty[0].name} while closing on target"
                        ),
                        "payload": {
                            "unit_id": str(u.id),
                            "artillery_id": str(same_side_arty[0].id),
                            "distance_to_target_m": round(dist_to_bombardment, 1),
                            "target_lat": arty_target["lat"],
                            "target_lon": arty_target["lon"],
                            "stage": "final_approach" if desired_stage == 2 else "closing",
                        },
                    })
                    task = new_task

            if dist_to_bombardment <= CEASEFIRE_REQUEST_RADIUS_M:
                # Halt the unit and request cease-fire
                new_task = dict(task)
                new_task["awaiting_ceasefire"] = True
                new_task["ceasefire_target"] = arty_target
                u.current_task = new_task

                # Signal artillery to finish current salvo and cease
                for arty in same_side_arty:
                    arty_t = arty.current_task
                    if arty_t and arty_t.get("type") == "fire":
                        arty_t_new = dict(arty_t)
                        # Set salvos to 1 so it fires this last round then stops
                        current_salvos = arty_t_new.get("salvos_remaining", 1)
                        arty_t_new["salvos_remaining"] = min(current_salvos, 1)
                        arty_t_new["ceasefire_requested_by"] = str(u.id)
                        arty.current_task = arty_t_new

                events.append({
                    "event_type": "ceasefire_requested",
                    "actor_unit_id": u.id,
                    "text_summary": (
                        f"{u.name} requesting cease-fire from {same_side_arty[0].name} — "
                        f"infantry approaching bombardment zone ({dist_to_bombardment:.0f}m)"
                    ),
                    "payload": {
                        "unit_id": str(u.id),
                        "artillery_id": str(same_side_arty[0].id),
                        "distance_to_target_m": round(dist_to_bombardment, 1),
                        "target_lat": arty_target["lat"],
                        "target_lon": arty_target["lon"],
                    },
                })
                break  # One ceasefire request per unit per tick

    return events


def process_artillery_support(
    all_units: list,
    terrain: TerrainService | None = None,
    under_fire: set | None = None,
    attacking_map: dict | None = None,
    fire_requests: list[dict] | None = None,
) -> list[dict]:
    """
    Auto-assign idle artillery units to support attacking units OR units under fire in their CoC.

    Walks up each requesting unit's parent chain to find artillery siblings
    (children of the same parent). If found and idle with ammo, assigns
    a fire task targeting the attacker's target or the source of incoming fire.

    Triggers:
      - Unit has attack/engage/fire task → support their attack
      - Unit is under fire (in under_fire set) → counter-battery / suppressive fire
      - Unit has auto_return_fire task → support their defensive fight
      - Fire request from a unit (e.g., "request artillery support on grid X")

    Args:
        all_units: All units in the session
        terrain: TerrainService for terrain queries
        under_fire: Set of unit IDs currently being attacked
        attacking_map: Dict mapping victim_id → list of attacker_ids (who is attacking whom)
        fire_requests: List of explicit fire support requests {unit_id, target_location, target_unit_id}

    Returns list of event dicts.
    """
    events = []
    if under_fire is None:
        under_fire = set()
    if fire_requests is None:
        fire_requests = []

    # Build lookup maps
    units_by_id = {str(u.id): u for u in all_units if not u.is_destroyed}
    children_by_parent = {}  # parent_id → [unit, ...]
    for u in all_units:
        if u.is_destroyed:
            continue
        pid = str(u.parent_unit_id) if u.parent_unit_id else None
        if pid:
            children_by_parent.setdefault(pid, []).append(u)

    # Keep linked support fire aligned with the supported unit's
    # current target or objective until cease-fire logic stops it.
    for arty in all_units:
        if arty.is_destroyed or arty.unit_type not in ARTILLERY_TYPES:
            continue
        arty_task = arty.current_task or {}
        if arty_task.get("type") != "fire" or not arty_task.get("sustained_support"):
            continue
        if arty_task.get("ceasefire_requested_by"):
            continue
        supported_unit = units_by_id.get(str(arty_task.get("support_for")))
        next_target_loc, next_target_uid = _resolve_supported_fire_target(
            arty_task,
            supported_unit,
            units_by_id,
        )
        if not next_target_loc:
            continue

        current_target = arty_task.get("target_location") or {}
        drift = _distance_m(
            float(current_target.get("lat", next_target_loc["lat"])),
            float(current_target.get("lon", next_target_loc["lon"])),
            next_target_loc["lat"],
            next_target_loc["lon"],
        )
        if drift > 35.0 or (
            next_target_uid and str(arty_task.get("target_unit_id") or "") != next_target_uid
        ):
            refreshed = dict(arty_task)
            refreshed["target_location"] = next_target_loc
            if next_target_uid:
                refreshed["target_unit_id"] = next_target_uid
            else:
                refreshed.pop("target_unit_id", None)
            arty.current_task = refreshed

    # Track which artillery units have already been tasked this tick
    tasked_artillery = set()

    # ── Process explicit fire requests FIRST (highest priority) ──
    for req in fire_requests:
        req_unit_id = req.get("unit_id")
        req_target_loc = req.get("target_location")
        req_target_uid = req.get("target_unit_id")
        req_fire_effect_type = req.get("fire_effect_type")
        req_smoke_duration_ticks = req.get("smoke_duration_ticks")
        req_coord_refs = [str(ref).lower() for ref in (req.get("coordination_unit_refs") or []) if ref]
        req_coord_ids = {str(ref) for ref in (req.get("coordination_unit_ids") or []) if ref}
        req_support_ids = {str(ref) for ref in (req.get("supporting_unit_ids") or []) if ref}
        
        if not req_unit_id or not req_target_loc:
            continue
            
        unit = units_by_id.get(str(req_unit_id))
        if not unit:
            continue
            
        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

        preferred_artillery: list = []
        if req_coord_ids or req_support_ids or req_coord_refs:
            for candidate in all_units:
                if candidate.is_destroyed:
                    continue
                if candidate.unit_type not in ARTILLERY_TYPES:
                    continue
                cand_side = candidate.side.value if hasattr(candidate.side, 'value') else str(candidate.side)
                if cand_side != unit_side:
                    continue
                cand_name = (candidate.name or "").lower()
                cand_id = str(candidate.id)
                if (
                    cand_id in req_coord_ids
                    or cand_id in req_support_ids
                    or any(
                    ref in cand_name
                    or cand_name in ref
                    or (ref == "mortar" and "мином" in cand_name)
                    or (ref == "миномёт" and "mortar" in cand_name)
                    or (ref == "миномет" and "mortar" in cand_name)
                    for ref in req_coord_refs
                    )
                ):
                    preferred_artillery.append(candidate)

        candidate_groups: list[list] = []
        if preferred_artillery:
            candidate_groups.append(preferred_artillery)

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
            candidate_groups.append(children_by_parent.get(parent_id, []))
            current_id = parent_id

        assigned = False
        for siblings in candidate_groups:
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

                # IMPORTANT: For explicit fire requests, override observe/standby tasks
                # The whole point of "be ready on request" is to respond to requests!
                sib_task = sib.current_task
                if sib_task:
                    sib_task_type = sib_task.get("type", "")
                    if sib_task_type in ("fire", "attack", "engage"):
                        # Already actively firing at something
                        if sib_task.get("target_location") or sib_task.get("target_unit_id"):
                            continue  # Actually busy

                # Check range
                sib_pos = _get_position(sib)
                if not sib_pos:
                    continue
                weapon_range = WEAPON_RANGE.get(sib.unit_type, 5000)
                dist = _distance_m(sib_pos[0], sib_pos[1], req_target_loc["lat"], req_target_loc["lon"])
                if dist > weapon_range:
                    continue

                # Danger-close applies to lethal fire, not smoke masking.
                if req_fire_effect_type != "smoke":
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
                        d_friendly = _distance_m(fu_pos[0], fu_pos[1], req_target_loc["lat"], req_target_loc["lon"])
                        if d_friendly <= DANGER_CLOSE_RADIUS_M:
                            friendly_danger = True
                            break
                    if friendly_danger:
                        continue

                # Assign fire mission (responding to explicit request)
                sib.current_task = {
                    "type": "fire",
                    "target_location": req_target_loc,
                    "target_unit_id": req_target_uid,
                    "support_for": str(unit.id),
                    "support_type": "smoke_request" if req_fire_effect_type == "smoke" else "fire_request",
                    "sustained_support": req_fire_effect_type != "smoke",
                    "salvos_remaining": 1 if req_fire_effect_type == "smoke" else DEFAULT_FIRE_SALVOS,
                }
                if req_fire_effect_type:
                    sib.current_task["fire_effect_type"] = req_fire_effect_type
                if req_smoke_duration_ticks:
                    sib.current_task["smoke_duration_ticks"] = req_smoke_duration_ticks
                tasked_artillery.add(str(sib.id))

                events.append({
                    "event_type": "artillery_support",
                    "actor_unit_id": sib.id,
                    "target_unit_id": uuid.UUID(req_target_uid) if req_target_uid else None,
                    "text_summary": (
                        f"{sib.name} responding to smoke request from {unit.name}"
                        if req_fire_effect_type == "smoke"
                        else f"{sib.name} responding to fire request from {unit.name}"
                    ),
                    "payload": {
                        "artillery_id": str(sib.id),
                        "supported_unit_id": str(unit.id),
                        "support_type": "smoke_request" if req_fire_effect_type == "smoke" else "fire_request",
                        "target_lat": req_target_loc["lat"],
                        "target_lon": req_target_loc["lon"],
                    },
                })
                assigned = True
                break  # One artillery per request
            if assigned:
                break

    # ── Existing auto-support logic for attacking/under-fire units ──
    # (rest of original code follows...)
    # Collect units that need artillery support
    # (attacking units + units under fire that have a target)
    requesting_units = []
    for unit in all_units:
        if unit.is_destroyed:
            continue
        task = unit.current_task
        if not task:
            # Unit is idle but under fire — look for attackers to counter
            if unit.id in under_fire:
                requesting_units.append((unit, None, None, "defensive_support"))
            continue
        task_type = task.get("type", "")
        if task_type in ("attack", "engage", "fire"):
            target_loc = task.get("target_location")
            target_uid = task.get("target_unit_id")
            requesting_units.append((unit, target_loc, target_uid, "offensive_support"))
        elif task_type == "defend" and unit.id in under_fire:
            # Defending unit under fire — needs suppressive support
            target_uid = task.get("target_unit_id")
            target_loc = task.get("target_location")
            requesting_units.append((unit, target_loc, target_uid, "defensive_support"))
        elif task.get("auto_return_fire") and unit.id in under_fire:
            # Unit auto-returning fire — also needs support
            target_uid = task.get("target_unit_id")
            target_loc = task.get("target_location")
            requesting_units.append((unit, target_loc, target_uid, "defensive_support"))

    for unit, target_loc, target_uid, support_type in requesting_units:
        # IMPORTANT: For attack/engage tasks, the target_location is where the unit is
        # GOING (destination), not where the enemy IS. We must NOT fire at the destination.
        # Instead, we need to find the actual enemy position from:
        # 1. For fire tasks: target_location is the actual fire target → use it
        # 2. For attack/engage WITH target_unit_id: look up enemy's CURRENT position from units_by_id
        # 3. For attack/engage WITHOUT target_unit_id: just movement destination → skip
        # 4. For defensive_support when under_fire: look up who's attacking us from the attacking_map
        task = unit.current_task
        task_type = task.get("type", "") if task else ""

        actual_fire_target = None
        actual_target_uid = target_uid  # May be None

        if task_type == "fire":
            # Fire tasks have explicit target location — use it
            actual_fire_target = target_loc
        elif task_type in ("attack", "engage") and target_uid:
            # Attack/engage task with a known target unit
            # Look up the ENEMY'S CURRENT position (not our movement destination)
            enemy_unit = units_by_id.get(str(target_uid))
            if enemy_unit and not enemy_unit.is_destroyed:
                enemy_pos = _get_position(enemy_unit)
                if enemy_pos:
                    actual_fire_target = {"lat": enemy_pos[0], "lon": enemy_pos[1]}
                    actual_target_uid = str(target_uid)
            # If we couldn't find the enemy, skip (fog of war - we don't know where they are)
            if not actual_fire_target:
                continue
        elif support_type == "defensive_support" and attacking_map:
            # Unit is under fire — find who's attacking us to counter-battery
            attackers = attacking_map.get(unit.id, [])
            if attackers:
                # Target the nearest attacker
                unit_pos = _get_position(unit)
                if unit_pos:
                    nearest = None
                    nearest_dist = float('inf')
                    for atk_id in attackers:
                        atk_unit = units_by_id.get(str(atk_id))
                        if atk_unit and not atk_unit.is_destroyed:
                            atk_pos = _get_position(atk_unit)
                            if atk_pos:
                                dist = _distance_m(unit_pos[0], unit_pos[1], atk_pos[0], atk_pos[1])
                                if dist < nearest_dist:
                                    nearest_dist = dist
                                    nearest = atk_unit
                    if nearest:
                        nearest_pos = _get_position(nearest)
                        if nearest_pos:
                            actual_fire_target = {"lat": nearest_pos[0], "lon": nearest_pos[1]}
                            actual_target_uid = str(nearest.id)
            if not actual_fire_target:
                continue
        else:
            # No target_unit_id and not a fire task — this is just movement destination
            continue

        if not actual_fire_target:
            continue

        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        linked_support_ids = {str(uid) for uid in ((task or {}).get("supporting_unit_ids") or []) if uid}

        candidate_groups: list[list] = []
        if linked_support_ids:
            candidate_groups.append([
                sib for sib in all_units
                if not sib.is_destroyed
                and sib.unit_type in ARTILLERY_TYPES
                and str(sib.id) in linked_support_ids
            ])

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
            candidate_groups.append(children_by_parent.get(parent_id, []))
            current_id = parent_id

        assigned = False
        for siblings in candidate_groups:
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
                if sib_task:
                    sib_task_type = sib_task.get("type", "")
                    # Skip units with explicit observe/standby task (player ordered to wait)
                    if sib_task_type == "observe" and sib_task.get("order_id"):
                        continue  # Explicitly told to stand by — don't auto-assign
                    if sib_task_type in ("fire", "attack", "engage"):
                        # If the fire task has a specific target, unit is busy
                        if sib_task.get("target_location") or sib_task.get("target_unit_id"):
                            continue  # Actually firing at a real target — skip

                # Check range
                sib_pos = _get_position(sib)
                if not sib_pos:
                    continue
                weapon_range = WEAPON_RANGE.get(sib.unit_type, 5000)
                dist = _distance_m(sib_pos[0], sib_pos[1], actual_fire_target["lat"], actual_fire_target["lon"])
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
                    d_friendly = _distance_m(fu_pos[0], fu_pos[1], actual_fire_target["lat"], actual_fire_target["lon"])
                    if d_friendly <= DANGER_CLOSE_RADIUS_M:
                        friendly_danger = True
                        break
                if friendly_danger:
                    continue

                # Assign fire mission
                sib.current_task = {
                    "type": "fire",
                    "target_location": actual_fire_target,
                    "target_unit_id": actual_target_uid,
                    "support_for": str(unit.id),
                    "support_type": support_type,
                    "sustained_support": True,
                    "salvos_remaining": DEFAULT_FIRE_SALVOS,
                }
                tasked_artillery.add(str(sib.id))

                support_label = "supporting" if support_type == "offensive_support" else "defending"
                events.append({
                    "event_type": "artillery_support",
                    "actor_unit_id": sib.id,
                    "target_unit_id": uuid.UUID(actual_target_uid) if actual_target_uid else None,
                    "text_summary": f"{sib.name} firing in support of {unit.name} ({support_label})",
                    "payload": {
                        "artillery_id": str(sib.id),
                        "supported_unit_id": str(unit.id),
                        "support_type": support_type,
                        "target_lat": actual_fire_target["lat"],
                        "target_lon": actual_fire_target["lon"],
                    },
                })
                assigned = True
                break  # One artillery unit per requesting unit per tick
            if assigned:
                break

    return events
