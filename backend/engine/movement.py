"""
Movement engine – moves units toward their task targets.

Uses formulas from AGENTS.MD Section 8.2:
  effective_speed = base_speed × terrain_factor × (1 - suppression × 0.7) × morale_factor
  distance_this_tick = effective_speed × tick_duration_seconds
"""

from __future__ import annotations

import math

from geoalchemy2.shape import to_shape, from_shape
from shapely.geometry import Point

from backend.engine.terrain import TerrainService


# Approximate meters per degree at mid-latitudes
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0  # ~cos(48°) * 111320


def _morale_factor(morale: float) -> float:
    if morale > 0.5:
        return 1.0
    elif morale >= 0.25:
        return 0.7
    else:
        return 0.4


def _distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in meters (flat-earth OK for short distances)."""
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _move_toward(
    lat: float, lon: float,
    target_lat: float, target_lon: float,
    distance_m: float,
) -> tuple[float, float, float]:
    """
    Move from (lat, lon) toward (target_lat, target_lon) by distance_m meters.
    Returns (new_lat, new_lon, heading_deg).
    """
    total_dist = _distance_m(lat, lon, target_lat, target_lon)
    if total_dist < 0.1:
        return target_lat, target_lon, 0.0

    fraction = min(distance_m / total_dist, 1.0)
    new_lat = lat + (target_lat - lat) * fraction
    new_lon = lon + (target_lon - lon) * fraction

    # Heading in degrees from north (0=N, 90=E)
    dy = (target_lat - lat) * METERS_PER_DEG_LAT
    dx = (target_lon - lon) * METERS_PER_DEG_LON_AT_48
    heading = math.degrees(math.atan2(dx, dy)) % 360

    return new_lat, new_lon, heading


def process_movement(
    units: list,
    tick_duration_sec: int,
    terrain: TerrainService,
) -> list[dict]:
    """
    Process movement for all units with movement tasks.

    Args:
        units: list of Unit ORM objects (will be mutated in-place)
        tick_duration_sec: seconds per tick
        terrain: TerrainService instance

    Returns:
        list of event dicts for movement events
    """
    events = []

    for unit in units:
        if unit.is_destroyed:
            continue

        task = unit.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        if task_type not in ("move", "attack", "advance"):
            continue

        target = task.get("target_location")
        if not target:
            continue

        target_lat = target.get("lat")
        target_lon = target.get("lon")
        if target_lat is None or target_lon is None:
            continue

        # Get current position
        if unit.position is None:
            continue

        try:
            pt = to_shape(unit.position)
            cur_lon, cur_lat = pt.x, pt.y
        except Exception:
            continue

        # Calculate effective speed
        terrain_factor = terrain.movement_factor(cur_lon, cur_lat)
        suppression = unit.suppression or 0.0
        morale = unit.morale or 1.0
        base_speed = unit.move_speed_mps or 4.0

        effective_speed = (
            base_speed
            * terrain_factor
            * (1.0 - suppression * 0.7)
            * _morale_factor(morale)
        )

        distance_this_tick = effective_speed * tick_duration_sec

        # Check if we'll arrive this tick
        remaining = _distance_m(cur_lat, cur_lon, target_lat, target_lon)

        if remaining <= distance_this_tick:
            # Arrived
            new_lat, new_lon = target_lat, target_lon
            dy = (target_lat - cur_lat) * METERS_PER_DEG_LAT
            dx = (target_lon - cur_lon) * METERS_PER_DEG_LON_AT_48
            heading = math.degrees(math.atan2(dx, dy)) % 360 if remaining > 1 else (unit.heading_deg or 0.0)

            unit.position = from_shape(Point(new_lon, new_lat), srid=4326)
            unit.heading_deg = heading

            # Complete the movement task
            if task_type == "move":
                unit.current_task = None
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} arrived at destination",
                    "payload": {"lat": new_lat, "lon": new_lon},
                })
            # For attack/advance, keep the task (combat will handle it)
        else:
            # Move toward target
            new_lat, new_lon, heading = _move_toward(
                cur_lat, cur_lon, target_lat, target_lon, distance_this_tick
            )
            unit.position = from_shape(Point(new_lon, new_lat), srid=4326)
            unit.heading_deg = heading

            events.append({
                "event_type": "movement",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} moving ({distance_this_tick:.0f}m this tick)",
                "payload": {
                    "from": {"lat": cur_lat, "lon": cur_lon},
                    "to": {"lat": new_lat, "lon": new_lon},
                    "remaining_m": remaining - distance_this_tick,
                    "speed_mps": effective_speed,
                },
            })

    return events

