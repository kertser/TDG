"""
Movement engine – moves units toward their task targets.

Uses formulas from AGENTS.MD Section 8.2:
  effective_speed = base_speed × terrain_factor × (1 - suppression × 0.7) × morale_factor
  distance_this_tick = effective_speed × tick_duration_seconds

Also handles obstacle interactions — map objects (minefields, wire, ditches)
slow movement and may deal damage to crossing units.
"""

from __future__ import annotations

import math

from geoalchemy2.shape import to_shape, from_shape
from shapely.geometry import Point, LineString

from backend.engine.terrain import TerrainService
from backend.engine.map_objects import MAP_OBJECT_DEFS


# Terrain types that provide meaningful cover (protection > 1.0)
COVER_TERRAIN_TYPES = {"forest", "urban", "scrub", "orchard", "mountain"}

# Search radius in degrees (~500m at mid-latitudes)
COVER_SEARCH_RADIUS_DEG = 0.005


def _find_nearest_cover(
    cur_lat: float,
    cur_lon: float,
    terrain: TerrainService,
) -> tuple[float, float] | None:
    """
    Find the nearest terrain cell that provides good cover.

    Searches nearby terrain cells (from the TerrainService's cell DB)
    for cover terrain types (forest, urban, scrub, orchard, mountain).
    Returns (lat, lon) of best cover position, or None if not found.
    """
    if not terrain._cells or not terrain._grid:
        # No cell data — try moving away from current position toward
        # best terrain based on protection factor at nearby sample points
        return _sample_cover_position(cur_lat, cur_lon, terrain)

    # Search through known terrain cells for the nearest cover
    best_dist = float("inf")
    best_pos = None

    for snail_path, terrain_type in terrain._cells.items():
        if terrain_type not in COVER_TERRAIN_TYPES:
            continue

        # Get centroid of this cell via grid service
        cell_lat, cell_lon = None, None
        try:
            center = terrain._grid.snail_to_center(snail_path)
            if center:
                cell_lon, cell_lat = center.x, center.y
        except Exception:
            continue

        if cell_lat is None or cell_lon is None:
            continue

        dist = _distance_m(cur_lat, cur_lon, cell_lat, cell_lon)
        # Only consider cells within ~800m
        if dist < best_dist and dist < 800:
            best_dist = dist
            best_pos = (cell_lat, cell_lon)

    # If no cell-based cover found within 800m, sample positions
    if best_pos is None:
        return _sample_cover_position(cur_lat, cur_lon, terrain)

    return best_pos


def _sample_cover_position(
    cur_lat: float,
    cur_lon: float,
    terrain: TerrainService,
) -> tuple[float, float] | None:
    """
    Sample 8 directions around current position (at 200m, 400m) and pick
    the one with the best protection factor.
    """
    best_protection = 0.0
    best_pos = None

    for dist_m in (200, 400):
        for angle_deg in range(0, 360, 45):
            angle_rad = math.radians(angle_deg)
            dlat = dist_m * math.cos(angle_rad) / METERS_PER_DEG_LAT
            dlon = dist_m * math.sin(angle_rad) / METERS_PER_DEG_LON_AT_48
            sample_lat = cur_lat + dlat
            sample_lon = cur_lon + dlon
            prot = terrain.protection_factor(sample_lon, sample_lat)
            if prot > best_protection:
                best_protection = prot
                best_pos = (sample_lat, sample_lon)

    # Only return if we found something significantly better than current
    cur_prot = terrain.protection_factor(cur_lon, cur_lat)
    if best_pos and best_protection > cur_prot + 0.15:
        return best_pos

    return None


# Approximate meters per degree at mid-latitudes
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Unit types that fire indirectly (should NOT advance toward fire target)
INDIRECT_FIRE_UNIT_TYPES = {
    "artillery_battery", "artillery_platoon",
    "mortar_section", "mortar_team",
}

# Unit types considered "vehicles" for obstacle passability
VEHICLE_UNIT_TYPES = {
    "tank_company", "tank_platoon", "tank_section",
    "mech_company", "mech_platoon", "mech_section",
    "avlb_vehicle", "avlb_section",
    "artillery_battery", "artillery_platoon",
    "logistics_unit",
    "headquarters",
}


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


def _is_vehicle(unit) -> bool:
    """Check if a unit is a vehicle type."""
    return unit.unit_type in VEHICLE_UNIT_TYPES


def _check_obstacles(
    cur_lat: float, cur_lon: float,
    target_lat: float, target_lon: float,
    unit,
    map_objects: list,
) -> tuple[float, float, list[dict]]:
    """
    Check movement path against active map objects (obstacles).

    Returns:
        (movement_factor, damage_total, events)
    """
    if not map_objects:
        return 1.0, 0.0, []

    is_veh = _is_vehicle(unit)
    move_factor = 1.0
    damage_total = 0.0
    events = []

    try:
        path_line = LineString([(cur_lon, cur_lat), (target_lon, target_lat)])
    except Exception:
        return 1.0, 0.0, []

    unit_point = Point(cur_lon, cur_lat)

    for obj in map_objects:
        if not obj.is_active:
            continue
        if obj.geometry is None:
            continue

        defn = MAP_OBJECT_DEFS.get(obj.object_type)
        if not defn or defn["category"] not in ("obstacle", "effect"):
            continue

        try:
            obj_shape = to_shape(obj.geometry)
        except Exception:
            continue

        intersects = False
        geom_type = obj_shape.geom_type

        if geom_type in ("Polygon", "MultiPolygon"):
            intersects = obj_shape.contains(unit_point) or path_line.intersects(obj_shape)
        elif geom_type in ("LineString", "MultiLineString"):
            effect_r = defn.get("effect_radius_m", 15)
            buffer_deg = effect_r / METERS_PER_DEG_LAT
            buffered = obj_shape.buffer(buffer_deg)
            intersects = buffered.contains(unit_point) or path_line.intersects(buffered)
        elif geom_type == "Point":
            effect_r = defn.get("effect_radius_m", 30)
            dist = _distance_m(cur_lat, cur_lon, obj_shape.y, obj_shape.x)
            intersects = dist <= effect_r

        if not intersects:
            continue

        if is_veh and not defn.get("vehicle_passable", True):
            move_factor = 0.0
            events.append({
                "event_type": "obstacle_blocked",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} blocked by {obj.label or obj.object_type} (impassable for vehicles)",
                "payload": {"object_type": obj.object_type, "object_id": str(obj.id)},
            })
            break
        elif not is_veh and not defn.get("infantry_passable", True):
            move_factor = 0.0
            events.append({
                "event_type": "obstacle_blocked",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} blocked by {obj.label or obj.object_type}",
                "payload": {"object_type": obj.object_type, "object_id": str(obj.id)},
            })
            break

        if is_veh:
            obj_move = defn.get("movement_factor_vehicle", 1.0)
        else:
            obj_move = defn.get("movement_factor_infantry", 1.0)
        move_factor = min(move_factor, obj_move)

        if is_veh:
            dmg = defn.get("damage_per_tick_vehicle", defn.get("damage_per_tick", 0.0))
        else:
            dmg = defn.get("damage_per_tick_infantry", defn.get("damage_per_tick", 0.0))
        if dmg > 0:
            damage_total += dmg
            events.append({
                "event_type": "obstacle_damage",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} taking damage from {obj.label or obj.object_type}",
                "payload": {
                    "object_type": obj.object_type,
                    "object_id": str(obj.id),
                    "damage": round(dmg, 4),
                },
            })

    return move_factor, damage_total, events


def _check_minefield_ahead(
    cur_lat: float, cur_lon: float,
    target_lat: float, target_lon: float,
    unit,
    map_objects: list,
) -> dict | None:
    """
    Check if the movement path crosses a DISCOVERED minefield for this unit's side.
    Returns an event dict if the unit should halt, or None if path is clear.
    Only checks minefields the unit's side has discovered (fog-of-war aware).
    """
    if not map_objects:
        return None

    try:
        path_line = LineString([(cur_lon, cur_lat), (target_lon, target_lat)])
    except Exception:
        return None

    unit_point = Point(cur_lon, cur_lat)
    side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

    for obj in map_objects:
        if not obj.is_active:
            continue
        if obj.object_type not in ("minefield", "at_minefield"):
            continue

        # Only avoid minefields the unit's side has discovered
        if side == "blue" and not obj.discovered_by_blue:
            continue
        if side == "red" and not obj.discovered_by_red:
            continue

        if obj.geometry is None:
            continue

        try:
            obj_shape = to_shape(obj.geometry)
        except Exception:
            continue

        # Check if the unit is already inside the minefield (don't halt — they need to get out)
        if obj_shape.contains(unit_point):
            continue

        # Check if path intersects the minefield
        geom_type = obj_shape.geom_type
        intersects = False
        if geom_type in ("Polygon", "MultiPolygon"):
            intersects = path_line.intersects(obj_shape)
        elif geom_type in ("LineString", "MultiLineString"):
            buffer_deg = 15 / METERS_PER_DEG_LAT
            intersects = path_line.intersects(obj_shape.buffer(buffer_deg))

        if intersects:
            label = obj.label or obj.object_type.replace('_', ' ')
            return {
                "event_type": "minefield_avoidance",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} halted — detected {label} ahead on route. Requesting engineers.",
                "payload": {
                    "object_type": obj.object_type,
                    "object_id": str(obj.id),
                    "reason": "minefield_detected",
                },
            }

    return None


def _check_water_crossing(
    cur_lat: float, cur_lon: float,
    target_lat: float, target_lon: float,
    unit,
    terrain: TerrainService,
    map_objects: list | None = None,
) -> dict | None:
    """
    Check if the movement path crosses water terrain without a bridge nearby.
    Returns an event dict if unit should halt, or None if path is clear.
    Infantry can ford shallow crossings (slower), vehicles cannot cross at all.
    """
    # Sample points along the path to check for water terrain
    dist = _distance_m(cur_lat, cur_lon, target_lat, target_lon)
    if dist < 1.0:
        return None

    # Check terrain at several points along the path
    steps = max(3, int(dist / 50))  # check every ~50m
    for i in range(1, steps + 1):
        frac = i / steps
        sample_lat = cur_lat + (target_lat - cur_lat) * frac
        sample_lon = cur_lon + (target_lon - cur_lon) * frac
        t = terrain.get_terrain_at(sample_lon, sample_lat)
        if t == "water":
            # Check if there's a bridge nearby (within 60m of this water point)
            bridge_nearby = False
            if map_objects:
                for obj in map_objects:
                    if not obj.is_active:
                        continue
                    if obj.object_type != "bridge_structure":
                        continue
                    if obj.geometry is None:
                        continue
                    try:
                        obj_shape = to_shape(obj.geometry)
                        bridge_lat, bridge_lon = obj_shape.centroid.y, obj_shape.centroid.x
                        bridge_dist = _distance_m(sample_lat, sample_lon, bridge_lat, bridge_lon)
                        if bridge_dist <= 60:
                            bridge_nearby = True
                            break
                    except Exception:
                        continue

            # Also check OSM-sourced bridge terrain cells
            if not bridge_nearby:
                bridge_t = terrain.get_terrain_at(sample_lon, sample_lat)
                if bridge_t == "bridge":
                    bridge_nearby = True

            if not bridge_nearby:
                is_veh = _is_vehicle(unit)
                if is_veh:
                    return {
                        "event_type": "water_blocked",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} halted — cannot cross river without bridge. Engineering bridge unit required.",
                        "payload": {
                            "reason": "water_no_bridge",
                            "water_lat": sample_lat,
                            "water_lon": sample_lon,
                        },
                    }
                else:
                    # Infantry blocked at deep water, needs bridge too
                    return {
                        "event_type": "water_blocked",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} halted — river crossing requires engineering bridge unit.",
                        "payload": {
                            "reason": "water_no_bridge",
                            "water_lat": sample_lat,
                            "water_lon": sample_lon,
                        },
                    }
    return None


def process_movement(
    units: list,
    tick_duration_sec: int,
    terrain: TerrainService,
    map_objects: list | None = None,
    weather_movement_mod: float = 1.0,
) -> list[dict]:
    """
    Process movement for all units with movement tasks.

    Args:
        units: list of Unit ORM objects (will be mutated in-place)
        tick_duration_sec: seconds per tick
        terrain: TerrainService instance
        map_objects: list of MapObject ORM objects for obstacle interaction

    Returns:
        list of event dicts for movement events
    """
    events = []

    # Get grid service for boundary check (if available)
    grid_service = terrain._grid if terrain else None

    for unit in units:
        if unit.is_destroyed:
            continue

        task = unit.current_task
        if not task:
            continue

        task_type = task.get("type", "")
        if task_type not in ("move", "attack", "advance", "engage", "fire", "disengage", "resupply"):
            continue

        # Indirect fire units (artillery/mortar) with "fire" task should NOT move
        # toward the target — they fire from their current position.
        if task_type == "fire" and unit.unit_type in INDIRECT_FIRE_UNIT_TYPES:
            continue

        # ── Awaiting cease-fire: halt until artillery clears ──
        if task.get("awaiting_ceasefire"):
            continue

        # ── Suppress role: hold position at weapon range, don't advance ──
        combat_role = task.get("combat_role")
        if combat_role == "suppress" and task_type in ("attack", "engage"):
            # Suppressing units stay at range — only move if out of weapon range
            from backend.engine.combat import WEAPON_RANGE, SUPPRESS_HOLD_RANGE_FRACTION
            weapon_range = WEAPON_RANGE.get(unit.unit_type, 800)
            hold_dist = weapon_range * SUPPRESS_HOLD_RANGE_FRACTION
            target_loc = task.get("target_location")
            if target_loc and unit.position is not None:
                try:
                    pt = to_shape(unit.position)
                    dist_to_target = _distance_m(pt.y, pt.x, target_loc["lat"], target_loc["lon"])
                    if dist_to_target <= hold_dist:
                        continue  # Already in suppression position — hold
                except Exception:
                    pass

        # ── Disengage: find nearest covered position if not yet assigned ──
        if task_type == "disengage" and not task.get("target_location"):
            if unit.position is None:
                continue
            try:
                pt = to_shape(unit.position)
                cur_lon, cur_lat = pt.x, pt.y
            except Exception:
                continue
            cover_target = _find_nearest_cover(cur_lat, cur_lon, terrain)
            if cover_target:
                task["target_location"] = {"lat": cover_target[0], "lon": cover_target[1]}
                unit.current_task = task  # mark dirty
            else:
                # No cover found nearby — stay put and defend
                unit.current_task = {"type": "defend", "order_id": task.get("order_id")}
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} disengaged, no cover found — holding position",
                    "payload": {},
                })
                continue

        target = task.get("target_location")
        if not target:
            continue

        target_lat = target.get("lat")
        target_lon = target.get("lon")
        if target_lat is None or target_lon is None:
            continue

        # ── Grid boundary check: don't move to targets outside the grid ──
        if grid_service and hasattr(grid_service, 'is_point_inside_grid'):
            if not grid_service.is_point_inside_grid(target_lat, target_lon):
                unit.current_task = None
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} — target is outside the area of operations",
                    "payload": {"reason": "target_outside_grid"},
                })
                continue

        # Get current position
        if unit.position is None:
            continue

        try:
            pt = to_shape(unit.position)
            cur_lon, cur_lat = pt.x, pt.y
        except Exception:
            continue

        # ── Check for discovered minefields ahead ──
        if map_objects:
            mine_event = _check_minefield_ahead(
                cur_lat, cur_lon, target_lat, target_lon, unit, map_objects
            )
            if mine_event:
                # Halt the unit — don't walk into the minefield
                unit.current_task = None
                events.append(mine_event)
                continue

        # ── Check for water/river crossing without bridge ──
        water_event = _check_water_crossing(
            cur_lat, cur_lon, target_lat, target_lon, unit, terrain, map_objects
        )
        if water_event:
            unit.current_task = None
            events.append(water_event)
            continue

        # Calculate effective speed
        terrain_factor = terrain.movement_factor(cur_lon, cur_lat)
        suppression = unit.suppression or 0.0
        morale = unit.morale or 1.0
        base_speed = unit.move_speed_mps or 4.0

        # Check obstacles along path
        obstacle_factor = 1.0
        obstacle_damage = 0.0
        if map_objects:
            obstacle_factor, obstacle_damage, obs_events = _check_obstacles(
                cur_lat, cur_lon, target_lat, target_lon, unit, map_objects or []
            )
            events.extend(obs_events)

            if obstacle_damage > 0:
                unit.strength = max(0.0, (unit.strength or 1.0) - obstacle_damage)
                if unit.strength <= 0.01:
                    unit.is_destroyed = True
                    unit.current_task = None
                    events.append({
                        "event_type": "unit_destroyed",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} destroyed by obstacle",
                        "payload": {"cause": "obstacle"},
                    })
                    continue

        if obstacle_factor <= 0.0:
            continue

        # Slope factor
        slope_factor = terrain.slope_movement_factor(cur_lon, cur_lat) if terrain else 1.0

        effective_speed = (
            base_speed
            * terrain_factor
            * obstacle_factor
            * slope_factor
            * weather_movement_mod
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
            elif task_type == "disengage":
                # Arrived at cover — switch to defend
                unit.current_task = {"type": "defend", "order_id": task.get("order_id")}
                events.append({
                    "event_type": "order_completed",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} disengaged and reached cover",
                    "payload": {"lat": new_lat, "lon": new_lon},
                })
            elif task_type == "resupply":
                # Arrived at supply source — keep task active for resupply engine
                # to process the actual resupply. Clear target_location so we stop moving.
                new_task = dict(task)
                new_task.pop("target_location", None)
                unit.current_task = new_task
                events.append({
                    "event_type": "movement",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} arrived at resupply point",
                    "payload": {"lat": new_lat, "lon": new_lon},
                })
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

