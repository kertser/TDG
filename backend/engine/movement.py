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

# Approximate meters per degree at mid-latitudes
METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0


def _find_nearest_cover(
    cur_lat: float,
    cur_lon: float,
    terrain: TerrainService,
    away_from: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """
    Find the nearest terrain cell that provides good cover.

    Searches nearby terrain cells (from the TerrainService's cell DB)
    for cover terrain types (forest, urban, scrub, orchard, mountain).
    Returns (lat, lon) of best cover position, or None if not found.

    If away_from=(lat, lon) is given, strongly prefer cover positions
    that are in the direction AWAY from that point (retreating from threat).
    """
    if not terrain._cells or not terrain._grid:
        return _sample_cover_position(cur_lat, cur_lon, terrain, away_from=away_from)

    # Compute "away" direction vector (if retreating from a threat)
    away_dx, away_dy = 0.0, 0.0
    has_away = False
    if away_from is not None:
        threat_lat, threat_lon = away_from
        away_dy = (cur_lat - threat_lat) * METERS_PER_DEG_LAT
        away_dx = (cur_lon - threat_lon) * METERS_PER_DEG_LON_AT_48
        away_len = math.sqrt(away_dx * away_dx + away_dy * away_dy)
        if away_len > 1.0:
            away_dx /= away_len
            away_dy /= away_len
            has_away = True

    best_score = float("-inf")
    best_pos = None

    for dist_m in (100, 200, 350, 500, 700):
        for angle_deg in range(0, 360, 30):  # 12 directions
            angle_rad = math.radians(angle_deg)
            dlat = dist_m * math.cos(angle_rad) / METERS_PER_DEG_LAT
            dlon = dist_m * math.sin(angle_rad) / METERS_PER_DEG_LON_AT_48
            sample_lat = cur_lat + dlat
            sample_lon = cur_lon + dlon
            t = terrain.get_terrain_at(sample_lon, sample_lat)
            if t in COVER_TERRAIN_TYPES:
                # Score: prefer closer cover, but strongly prefer "away from enemy" direction
                score = 1000.0 - dist_m  # base: closer is better
                if has_away:
                    # Dot product of (unit→sample) direction with "away" direction
                    dir_dy = dlat * METERS_PER_DEG_LAT
                    dir_dx = dlon * METERS_PER_DEG_LON_AT_48
                    dir_len = math.sqrt(dir_dx * dir_dx + dir_dy * dir_dy)
                    if dir_len > 1:
                        cos_angle = (dir_dx * away_dx + dir_dy * away_dy) / dir_len
                        # cos_angle: 1.0 = directly away from enemy (good)
                        #            0.0 = perpendicular
                        #           -1.0 = toward enemy (bad)
                        score += cos_angle * 800  # strong direction bias
                if score > best_score:
                    best_score = score
                    best_pos = (sample_lat, sample_lon)

        # If we found decent cover at this radius, don't search further
        # (but only if it's in a reasonable direction away from enemy)
        if best_pos is not None and (not has_away or best_score > 500):
            return best_pos

    if best_pos is not None:
        return best_pos

    # Fallback to sampling with protection factor
    return _sample_cover_position(cur_lat, cur_lon, terrain, away_from=away_from)


def _sample_cover_position(
    cur_lat: float,
    cur_lon: float,
    terrain: TerrainService,
    away_from: tuple[float, float] | None = None,
) -> tuple[float, float] | None:
    """
    Sample 8 directions around current position (at 200m, 400m) and pick
    the one with the best protection factor, biased away from threat.
    """
    # Compute "away" direction vector
    away_dx, away_dy = 0.0, 0.0
    has_away = False
    if away_from is not None:
        threat_lat, threat_lon = away_from
        away_dy = (cur_lat - threat_lat) * METERS_PER_DEG_LAT
        away_dx = (cur_lon - threat_lon) * METERS_PER_DEG_LON_AT_48
        away_len = math.sqrt(away_dx * away_dx + away_dy * away_dy)
        if away_len > 1.0:
            away_dx /= away_len
            away_dy /= away_len
            has_away = True

    best_score = float("-inf")
    best_pos = None

    for dist_m in (200, 400):
        for angle_deg in range(0, 360, 45):
            angle_rad = math.radians(angle_deg)
            dlat = dist_m * math.cos(angle_rad) / METERS_PER_DEG_LAT
            dlon = dist_m * math.sin(angle_rad) / METERS_PER_DEG_LON_AT_48
            sample_lat = cur_lat + dlat
            sample_lon = cur_lon + dlon
            prot = terrain.protection_factor(sample_lon, sample_lat)
            score = prot
            if has_away:
                dir_dy = dlat * METERS_PER_DEG_LAT
                dir_dx = dlon * METERS_PER_DEG_LON_AT_48
                dir_len = math.sqrt(dir_dx * dir_dx + dir_dy * dir_dy)
                if dir_len > 1:
                    cos_angle = (dir_dx * away_dx + dir_dy * away_dy) / dir_len
                    score += cos_angle * 1.5  # direction bias
            if score > best_score:
                best_score = score
                best_pos = (sample_lat, sample_lon)

    # Only return if we found something significantly better than current
    cur_prot = terrain.protection_factor(cur_lon, cur_lat)
    if best_pos:
        if has_away:
            # When retreating, always pick something (even if protection isn't better)
            return best_pos
        elif best_score > cur_prot + 0.15:
            return best_pos

    # Last resort when retreating: just move 400m directly away from enemy
    if has_away:
        retreat_lat = cur_lat + away_dy * 400 / METERS_PER_DEG_LAT
        retreat_lon = cur_lon + away_dx * 400 / METERS_PER_DEG_LON_AT_48
        return (retreat_lat, retreat_lon)

    return None


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
        if task_type not in ("move", "attack", "advance", "engage", "fire", "disengage", "withdraw", "resupply"):
            continue

        # Indirect fire units (artillery/mortar) with "fire" task should NOT move
        # toward the target — they fire from their current position.
        if task_type == "fire" and unit.unit_type in INDIRECT_FIRE_UNIT_TYPES:
            continue

        # Auto-return-fire units should NOT advance toward the attacker.
        # They fire from their current position. This prevents the defending
        # side from inadvertently charging toward the attackers.
        if task.get("auto_return_fire"):
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

        # ── Disengage/Withdraw: find nearest covered position AWAY FROM enemy ──
        if task_type in ("disengage", "withdraw") and not task.get("target_location"):
            if unit.position is None:
                continue
            try:
                pt = to_shape(unit.position)
                cur_lon, cur_lat = pt.x, pt.y
            except Exception:
                continue

            # Find the nearest enemy unit to determine retreat direction
            unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
            nearest_enemy_pos = None
            nearest_enemy_dist = float("inf")
            for other in units:
                if other.is_destroyed or other.position is None:
                    continue
                other_side = other.side.value if hasattr(other.side, 'value') else str(other.side)
                if other_side == unit_side:
                    continue  # same side
                try:
                    other_pt = to_shape(other.position)
                    d = _distance_m(cur_lat, cur_lon, other_pt.y, other_pt.x)
                    if d < nearest_enemy_dist:
                        nearest_enemy_dist = d
                        nearest_enemy_pos = (other_pt.y, other_pt.x)
                except Exception:
                    continue

            # Search for cover AWAY from the enemy
            cover_target = _find_nearest_cover(
                cur_lat, cur_lon, terrain,
                away_from=nearest_enemy_pos,
            )
            if cover_target:
                new_task = dict(task)  # copy for JSONB change detection
                new_task["target_location"] = {"lat": cover_target[0], "lon": cover_target[1]}
                unit.current_task = new_task
            else:
                # No cover found nearby — if we know where the enemy is, just retreat 400m away
                if nearest_enemy_pos is not None:
                    away_dy = (cur_lat - nearest_enemy_pos[0]) * METERS_PER_DEG_LAT
                    away_dx = (cur_lon - nearest_enemy_pos[1]) * METERS_PER_DEG_LON_AT_48
                    away_len = math.sqrt(away_dx * away_dx + away_dy * away_dy)
                    if away_len > 1.0:
                        retreat_lat = cur_lat + (away_dy / away_len) * 400 / METERS_PER_DEG_LAT
                        retreat_lon = cur_lon + (away_dx / away_len) * 400 / METERS_PER_DEG_LON_AT_48
                        new_task = dict(task)
                        new_task["target_location"] = {"lat": retreat_lat, "lon": retreat_lon}
                        unit.current_task = new_task
                    else:
                        # Enemy is right on top of us — hold and defend
                        unit.current_task = {
                            "type": "defend",
                            "order_id": task.get("order_id"),
                            "disengaging": True,
                        }
                        events.append({
                            "event_type": "order_completed",
                            "actor_unit_id": unit.id,
                            "text_summary": f"{unit.name} disengaged, no cover found — holding position",
                            "payload": {},
                        })
                        continue
                else:
                    # No enemy detected nearby — just hold position and defend
                    unit.current_task = {
                        "type": "defend",
                        "order_id": task.get("order_id"),
                        "disengaging": True,
                    }
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

        # ── Waypoint-following: determine immediate movement target ──
        # If the task has waypoints, move toward the next waypoint
        # instead of the final target_location.
        waypoints = task.get("waypoints")
        immediate_lat, immediate_lon = target_lat, target_lon
        using_waypoints = False

        if waypoints and isinstance(waypoints, list) and len(waypoints) > 0:
            # ── Stale destination check ──
            # If the last waypoint doesn't match the current target_location
            # (target moved, e.g. enemy unit repositioned), fix or discard waypoints.
            last_wp = waypoints[-1]
            last_wp_lat = last_wp[0] if isinstance(last_wp, (list, tuple)) else last_wp.get("lat", 0)
            last_wp_lon = last_wp[1] if isinstance(last_wp, (list, tuple)) else last_wp.get("lon", 0)
            dest_drift = _distance_m(last_wp_lat, last_wp_lon, target_lat, target_lon)
            if dest_drift > 500:
                # Target moved a lot — discard all waypoints, use straight-line
                waypoints = []
            elif dest_drift > 80:
                # Target drifted moderately — update last waypoint to current target
                waypoints[-1] = [target_lat, target_lon]

            # Pop waypoints that we've already passed:
            # 1. Within ~40m proximity threshold
            # 2. "Behind" the unit (farther from destination than unit is)
            # 3. Would cause backward/sideways movement (> ~80° from destination)
            unit_to_dest = _distance_m(cur_lat, cur_lon, target_lat, target_lon)
            # Pre-compute direction toward destination for angle checks
            dx_dest = (target_lon - cur_lon) * METERS_PER_DEG_LON_AT_48
            dy_dest = (target_lat - cur_lat) * METERS_PER_DEG_LAT
            len_dest = math.sqrt(dx_dest * dx_dest + dy_dest * dy_dest)

            is_first_wp = True  # Track whether we're checking the first waypoint
            while len(waypoints) > 0:
                wp = waypoints[0]
                wp_lat = wp[0] if isinstance(wp, (list, tuple)) else wp.get("lat", 0)
                wp_lon = wp[1] if isinstance(wp, (list, tuple)) else wp.get("lon", 0)
                dist_to_wp = _distance_m(cur_lat, cur_lon, wp_lat, wp_lon)
                # Close enough — already passed
                if dist_to_wp < 40:
                    waypoints.pop(0)
                    is_first_wp = False
                    continue
                # Waypoint is farther from destination than the unit —
                # the unit has progressed past it (tight 2% tolerance)
                wp_to_dest = _distance_m(wp_lat, wp_lon, target_lat, target_lon)
                if wp_to_dest > unit_to_dest * 1.02 and len(waypoints) > 1:
                    waypoints.pop(0)
                    is_first_wp = False
                    continue

                # ── Direction check: skip waypoint that takes us backward/sideways ──
                if len(waypoints) > 1 and dist_to_wp > 50 and len_dest > 50:
                    dx_wp = (wp_lon - cur_lon) * METERS_PER_DEG_LON_AT_48
                    dy_wp = (wp_lat - cur_lat) * METERS_PER_DEG_LAT
                    len_wp = math.sqrt(dx_wp * dx_wp + dy_wp * dy_wp)
                    if len_wp > 10:
                        cos_angle = (dx_wp * dx_dest + dy_wp * dy_dest) / (len_wp * len_dest)
                        # First waypoint: be very aggressive (cos < 0.25 = ~75°)
                        # to prevent backward movement arrows
                        threshold = 0.25 if is_first_wp else 0.0
                        if cos_angle < threshold:
                            waypoints.pop(0)
                            is_first_wp = False
                            continue

                break

            if len(waypoints) > 0:
                wp = waypoints[0]
                immediate_lat = wp[0] if isinstance(wp, (list, tuple)) else wp.get("lat", 0)
                immediate_lon = wp[1] if isinstance(wp, (list, tuple)) else wp.get("lon", 0)
                using_waypoints = True

            # Update waypoints in task (mutate JSONB)
            new_task = dict(task)
            new_task["waypoints"] = waypoints
            unit.current_task = new_task
            task = new_task

        # ── Check for discovered minefields ahead ──
        if map_objects:
            mine_event = _check_minefield_ahead(
                cur_lat, cur_lon, immediate_lat, immediate_lon, unit, map_objects
            )
            if mine_event:
                # Halt the unit — don't walk into the minefield
                unit.current_task = None
                events.append(mine_event)
                continue

        # ── Check for water/river crossing without bridge ──
        water_event = _check_water_crossing(
            cur_lat, cur_lon, immediate_lat, immediate_lon, unit, terrain, map_objects
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
                cur_lat, cur_lon, immediate_lat, immediate_lon, unit, map_objects or []
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

        # Check if we'll arrive at the immediate target this tick
        remaining_to_wp = _distance_m(cur_lat, cur_lon, immediate_lat, immediate_lon)
        remaining_to_final = _distance_m(cur_lat, cur_lon, target_lat, target_lon)

        # If following waypoints, we may pass through multiple waypoints in one tick
        distance_left = distance_this_tick
        move_lat, move_lon = cur_lat, cur_lon
        _heading = unit.heading_deg or 0.0

        if using_waypoints:
            while distance_left > 0 and waypoints and len(waypoints) > 0:
                wp = waypoints[0]
                wp_lat = wp[0] if isinstance(wp, (list, tuple)) else wp.get("lat", 0)
                wp_lon = wp[1] if isinstance(wp, (list, tuple)) else wp.get("lon", 0)
                dist_to_wp = _distance_m(move_lat, move_lon, wp_lat, wp_lon)

                if dist_to_wp <= distance_left:
                    # Reach this waypoint, advance to next
                    # Compute heading from this segment (before updating position)
                    dy = (wp_lat - move_lat) * METERS_PER_DEG_LAT
                    dx = (wp_lon - move_lon) * METERS_PER_DEG_LON_AT_48
                    if dist_to_wp > 1:
                        _heading = math.degrees(math.atan2(dx, dy)) % 360
                    move_lat, move_lon = wp_lat, wp_lon
                    distance_left -= dist_to_wp
                    waypoints.pop(0)
                else:
                    # Move partway toward this waypoint
                    move_lat, move_lon, _heading = _move_toward(
                        move_lat, move_lon, wp_lat, wp_lon, distance_left
                    )
                    distance_left = 0

            # Update waypoints in task
            new_task = dict(task)
            new_task["waypoints"] = waypoints
            unit.current_task = new_task

        # After waypoint traversal, check if we're close to the final target
        remaining_to_final = _distance_m(move_lat, move_lon, target_lat, target_lon)
        wp_exhausted = not waypoints or len(waypoints) == 0

        if wp_exhausted and remaining_to_final <= max(distance_left, 30):
            # Arrived at final destination
            new_lat, new_lon = target_lat, target_lon
            # Use move_lat/move_lon (position after traversal) for heading, not cur_lat/cur_lon
            dy = (target_lat - move_lat) * METERS_PER_DEG_LAT
            dx = (target_lon - move_lon) * METERS_PER_DEG_LON_AT_48
            heading = math.degrees(math.atan2(dx, dy)) % 360 if remaining_to_final > 1 else _heading

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
            elif task_type in ("disengage", "withdraw"):
                # Arrived at cover — switch to defend, but keep disengaging flag
                # so auto-return-fire won't immediately override the retreat order
                unit.current_task = {
                    "type": "defend",
                    "order_id": task.get("order_id"),
                    "disengaging": True,
                }
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
                new_task.pop("waypoints", None)
                unit.current_task = new_task
                events.append({
                    "event_type": "movement",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name} arrived at resupply point",
                    "payload": {"lat": new_lat, "lon": new_lon},
                })
        elif not using_waypoints:
            # No waypoints — use original straight-line movement
            if remaining_to_final <= distance_this_tick:
                new_lat, new_lon = target_lat, target_lon
                dy = (target_lat - cur_lat) * METERS_PER_DEG_LAT
                dx = (target_lon - cur_lon) * METERS_PER_DEG_LON_AT_48
                heading = math.degrees(math.atan2(dx, dy)) % 360 if remaining_to_final > 1 else (unit.heading_deg or 0.0)

                unit.position = from_shape(Point(new_lon, new_lat), srid=4326)
                unit.heading_deg = heading

                if task_type == "move":
                    unit.current_task = None
                    events.append({
                        "event_type": "order_completed",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} arrived at destination",
                        "payload": {"lat": new_lat, "lon": new_lon},
                    })
                elif task_type in ("disengage", "withdraw"):
                    unit.current_task = {"type": "defend", "order_id": task.get("order_id"), "disengaging": True}
                    events.append({
                        "event_type": "order_completed",
                        "actor_unit_id": unit.id,
                        "text_summary": f"{unit.name} disengaged and reached cover",
                        "payload": {"lat": new_lat, "lon": new_lon},
                    })
                elif task_type == "resupply":
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
                        "remaining_m": remaining_to_final - distance_this_tick,
                        "speed_mps": effective_speed,
                    },
                })
        else:
            # Waypoints mode: moved along waypoints but not yet at final destination
            unit.position = from_shape(Point(move_lon, move_lat), srid=4326)
            unit.heading_deg = _heading

            events.append({
                "event_type": "movement",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} moving ({distance_this_tick:.0f}m this tick)",
                "payload": {
                    "from": {"lat": cur_lat, "lon": cur_lon},
                    "to": {"lat": move_lat, "lon": move_lon},
                    "remaining_m": remaining_to_final,
                    "speed_mps": effective_speed,
                },
            })

    return events

