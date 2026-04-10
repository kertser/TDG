"""
Resupply engine — handles resupply from supply caches and logistics units.

Mechanics:
  - Supply cache map objects resupply friendly units within 50m (ammo + minor strength).
  - Logistics units (logistics_platoon, logistics_section) act as mobile supply points:
    they resupply all friendly units within 50m each tick.
  - Units with a "resupply" task automatically move to the nearest supply source
    (supply cache or logistics unit) and resupply when within 50m.
  - Resupply rates: supply_cache gives +0.10 ammo/tick, logistics unit gives +0.08 ammo/tick.
  - A unit is considered "resupplied" when ammo ≥ 0.95.
"""

from __future__ import annotations

import math

from geoalchemy2.shape import to_shape

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Resupply constants
RESUPPLY_RADIUS_M = 50.0           # proximity required for resupply
SUPPLY_CACHE_AMMO_RATE = 0.10      # ammo restored per tick from supply cache
SUPPLY_CACHE_STRENGTH_RATE = 0.005 # strength restored per tick from supply cache
LOGISTICS_UNIT_AMMO_RATE = 0.08    # ammo restored per tick from logistics unit
LOGISTICS_UNIT_STRENGTH_RATE = 0.003
RESUPPLY_COMPLETE_THRESHOLD = 0.95 # ammo level at which resupply is considered complete

# Unit types that can act as mobile supply points
LOGISTICS_UNIT_TYPES = {
    "logistics_platoon", "logistics_section",
    "supply_platoon", "supply_section",
}


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _get_position(unit) -> tuple[float, float] | None:
    if unit.position is None:
        return None
    try:
        pt = to_shape(unit.position)
        return pt.y, pt.x  # lat, lon
    except Exception:
        return None


def _get_centroid(geom_wkb) -> tuple[float, float] | None:
    try:
        shape = to_shape(geom_wkb)
        c = shape.centroid
        return c.y, c.x
    except Exception:
        return None


def _is_logistics_unit(unit) -> bool:
    """Check if unit is a logistics/supply unit type."""
    return getattr(unit, 'unit_type', '') in LOGISTICS_UNIT_TYPES


def find_nearest_supply_source(
    unit,
    all_units: list,
    map_objects: list,
) -> dict | None:
    """
    Find the nearest supply source (supply_cache map object or logistics unit)
    for the given unit.

    Returns dict with {type, lat, lon, distance_m, id} or None.
    """
    unit_pos = _get_position(unit)
    if unit_pos is None:
        return None

    unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
    best = None
    best_dist = float('inf')

    # Check supply cache map objects
    for obj in map_objects:
        if not obj.is_active:
            continue
        if obj.object_type != "supply_cache":
            continue
        # Side check: neutral supply caches work for all
        obj_side = obj.side.value if hasattr(obj.side, 'value') else str(obj.side) if obj.side else 'neutral'
        if obj_side != "neutral" and obj_side != unit_side:
            continue
        pos = _get_centroid(obj.geometry)
        if pos is None:
            continue
        dist = _distance_m(unit_pos[0], unit_pos[1], pos[0], pos[1])
        if dist < best_dist:
            best_dist = dist
            best = {
                "type": "supply_cache",
                "lat": pos[0],
                "lon": pos[1],
                "distance_m": dist,
                "id": str(obj.id),
            }

    # Check logistics units on the same side
    for u in all_units:
        if u.is_destroyed or u.id == unit.id:
            continue
        if not _is_logistics_unit(u):
            continue
        u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
        if u_side != unit_side:
            continue
        u_pos = _get_position(u)
        if u_pos is None:
            continue
        dist = _distance_m(unit_pos[0], unit_pos[1], u_pos[0], u_pos[1])
        if dist < best_dist:
            best_dist = dist
            best = {
                "type": "logistics_unit",
                "lat": u_pos[0],
                "lon": u_pos[1],
                "distance_m": dist,
                "id": str(u.id),
            }

    return best


def process_resupply(
    all_units: list,
    map_objects: list,
) -> list[dict]:
    """
    Process resupply effects each tick.

    1. Supply cache map objects resupply all friendly units within 50m.
    2. Logistics units resupply all friendly units within 50m.
    3. Units with "resupply" task: if within 50m of a supply source, resupply.
       If not, set target_location to the nearest supply source so movement
       engine moves them there.

    Returns list of event dicts.
    """
    events = []

    # Build lookup of supply cache positions
    supply_caches = []
    for obj in map_objects:
        if not obj.is_active or obj.object_type != "supply_cache":
            continue
        pos = _get_centroid(obj.geometry)
        if pos is None:
            continue
        obj_side = obj.side.value if hasattr(obj.side, 'value') else str(obj.side) if obj.side else 'neutral'
        supply_caches.append({
            "pos": pos,
            "side": obj_side,
            "label": obj.label or "supply cache",
            "id": str(obj.id),
        })

    # Build lookup of logistics units
    logistics_units = []
    for u in all_units:
        if u.is_destroyed or not _is_logistics_unit(u):
            continue
        pos = _get_position(u)
        if pos is None:
            continue
        u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
        logistics_units.append({
            "unit": u,
            "pos": pos,
            "side": u_side,
        })

    for unit in all_units:
        if unit.is_destroyed:
            continue
        unit_pos = _get_position(unit)
        if unit_pos is None:
            continue
        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        ammo = unit.ammo if unit.ammo is not None else 1.0

        # Skip units that are already full on ammo
        if ammo >= 1.0:
            # If unit has a resupply task and is full, complete it
            task = unit.current_task
            if task and task.get("type") == "resupply":
                unit.current_task = None
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} resupply complete — fully loaded",
                    "payload": {"task_type": "resupply", "ammo": 1.0},
                })
            continue

        # --- Check proximity to supply caches ---
        resupplied_from_cache = False
        for sc in supply_caches:
            if sc["side"] != "neutral" and sc["side"] != unit_side:
                continue
            dist = _distance_m(unit_pos[0], unit_pos[1], sc["pos"][0], sc["pos"][1])
            if dist <= RESUPPLY_RADIUS_M:
                old_ammo = ammo
                ammo = min(1.0, ammo + SUPPLY_CACHE_AMMO_RATE)
                unit.ammo = ammo
                strength = unit.strength if unit.strength is not None else 1.0
                if strength < 1.0:
                    unit.strength = min(1.0, strength + SUPPLY_CACHE_STRENGTH_RATE)
                resupplied_from_cache = True
                if old_ammo < 0.5 and ammo >= 0.5:
                    events.append({
                        "event_type": "resupply",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} resupplied from {sc['label']}",
                        "payload": {
                            "source": "supply_cache",
                            "source_id": sc["id"],
                            "ammo": round(ammo, 3),
                        },
                    })
                break  # only resupply from one cache per tick

        # --- Check proximity to logistics units ---
        if not resupplied_from_cache and not _is_logistics_unit(unit):
            for lu in logistics_units:
                if lu["side"] != unit_side:
                    continue
                dist = _distance_m(unit_pos[0], unit_pos[1], lu["pos"][0], lu["pos"][1])
                if dist <= RESUPPLY_RADIUS_M:
                    old_ammo = ammo
                    ammo = min(1.0, ammo + LOGISTICS_UNIT_AMMO_RATE)
                    unit.ammo = ammo
                    strength = unit.strength if unit.strength is not None else 1.0
                    if strength < 1.0:
                        unit.strength = min(1.0, strength + LOGISTICS_UNIT_STRENGTH_RATE)
                    if old_ammo < 0.5 and ammo >= 0.5:
                        events.append({
                            "event_type": "resupply",
                            "actor_unit_id": unit.id,
                            "text_summary": f"{unit.name} resupplied by {lu['unit'].name}",
                            "payload": {
                                "source": "logistics_unit",
                                "source_id": str(lu["unit"].id),
                                "ammo": round(ammo, 3),
                            },
                        })
                    break

        # --- Handle resupply task: set movement target to nearest supply source ---
        task = unit.current_task
        if task and task.get("type") == "resupply":
            # Check if already at a supply source
            at_source = False
            for sc in supply_caches:
                if sc["side"] != "neutral" and sc["side"] != unit_side:
                    continue
                dist = _distance_m(unit_pos[0], unit_pos[1], sc["pos"][0], sc["pos"][1])
                if dist <= RESUPPLY_RADIUS_M:
                    at_source = True
                    break
            if not at_source:
                for lu in logistics_units:
                    if lu["side"] != unit_side:
                        continue
                    dist = _distance_m(unit_pos[0], unit_pos[1], lu["pos"][0], lu["pos"][1])
                    if dist <= RESUPPLY_RADIUS_M:
                        at_source = True
                        break

            if at_source and ammo >= RESUPPLY_COMPLETE_THRESHOLD:
                # Resupply complete
                unit.current_task = None
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} resupply complete — ammo at {ammo:.0%}",
                    "payload": {"task_type": "resupply", "ammo": round(ammo, 3)},
                })
            elif not at_source and not task.get("target_location"):
                # Need to find a supply source and set movement target
                source = find_nearest_supply_source(unit, all_units, map_objects)
                if source:
                    new_task = dict(task)
                    new_task["target_location"] = {
                        "lat": source["lat"],
                        "lon": source["lon"],
                    }
                    new_task["supply_source"] = source["type"]
                    new_task["supply_source_id"] = source["id"]
                    unit.current_task = new_task
                else:
                    # No supply source found
                    events.append({
                        "event_type": "resupply_failed",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name}: no supply source found nearby",
                        "payload": {"reason": "no_supply_source"},
                    })

    # --- Logistics units with "resupply" task targeting a location/unit ---
    # Logistics units ordered to resupply at a location move there,
    # and any nearby friendly units get resupplied automatically (handled above).
    # No extra logic needed — the movement engine moves them, and once within 50m,
    # process_resupply handles the rest.

    return events


