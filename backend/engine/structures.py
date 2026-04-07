"""
Structure effects engine — processes resupply, protection bonuses, and comms bonuses
from structures (supply_cache, field_hospital, command_post_structure, etc.)

Called once per tick in the tick sequence.
"""

from __future__ import annotations

import math

from geoalchemy2.shape import to_shape
from backend.engine.map_objects import MAP_OBJECT_DEFS

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0


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
    """Get centroid lat/lon from a PostGIS geometry WKB."""
    try:
        shape = to_shape(geom_wkb)
        c = shape.centroid
        return c.y, c.x
    except Exception:
        return None


def process_structures(
    all_units: list,
    map_objects: list,
) -> list[dict]:
    """
    Apply structure effects to nearby friendly units.

    Effects:
      - supply_cache: restore ammo and minor strength
      - field_hospital: restore strength
      - supply_cache: restore ammo + strength
      - command_post_structure: prevent comms degradation
      - observation_tower: detection bonus (handled in detection.py)
      - pillbox/entrenchment: protection bonus (handled in combat.py)

    Returns list of event dicts.
    """
    events = []

    for obj in map_objects:
        if not obj.is_active:
            continue

        defn = MAP_OBJECT_DEFS.get(obj.object_type)
        if not defn:
            continue

        # Only structures provide resupply/comms bonuses
        if defn["category"] != "structure":
            continue

        resupply = defn.get("resupply")
        comms_bonus = defn.get("comms_bonus", False)

        if not resupply and not comms_bonus:
            continue

        # Get structure position
        if obj.geometry is None:
            continue
        struct_pos = _get_centroid(obj.geometry)
        if struct_pos is None:
            continue

        effect_radius = defn.get("effect_radius_m", 100)
        obj_side = obj.side.value if hasattr(obj.side, 'value') else str(obj.side)

        for unit in all_units:
            if unit.is_destroyed:
                continue

            unit_pos = _get_position(unit)
            if unit_pos is None:
                continue

            # Side check: neutral structures affect all, sided structures only affect same side
            unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
            if obj_side != "neutral" and obj_side != unit_side:
                continue

            dist = _distance_m(struct_pos[0], struct_pos[1], unit_pos[0], unit_pos[1])
            if dist > effect_radius:
                continue

            # Apply resupply
            if resupply:
                ammo_rate = resupply.get("ammo", 0)
                str_rate = resupply.get("strength", 0)

                if ammo_rate > 0 and (unit.ammo or 0) < 1.0:
                    old_ammo = unit.ammo or 0
                    unit.ammo = min(1.0, old_ammo + ammo_rate)
                    if old_ammo < 0.5 and unit.ammo >= 0.5:
                        events.append({
                            "event_type": "resupply",
                            "actor_unit_id": unit.id,
                            "text_summary": f"{unit.name} resupplied ammo from {obj.label or obj.object_type}",
                            "payload": {
                                "unit_id": str(unit.id),
                                "object_type": obj.object_type,
                                "ammo": round(unit.ammo, 3),
                            },
                        })

                if str_rate > 0 and (unit.strength or 0) < 1.0:
                    old_str = unit.strength or 0
                    unit.strength = min(1.0, old_str + str_rate)

            # Apply comms bonus
            if comms_bonus:
                from backend.models.unit import CommsStatus
                if unit.comms_status != CommsStatus.operational:
                    unit.comms_status = CommsStatus.operational
                    events.append({
                        "event_type": "comms_change",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} comms restored by {obj.label or 'command post'}",
                        "payload": {
                            "unit_id": str(unit.id),
                            "new_status": "operational",
                        },
                    })

    return events

