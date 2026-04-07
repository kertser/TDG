"""
Engineering engine — processes engineering unit special actions.

Engineering tasks (stored in unit.current_task):
  - breach:    {type: "breach", target_object_id: UUID}
               Engineer works on breaching obstacle; progress tracked in MapObject.properties.breach_progress
  - lay_mines: {type: "lay_mines", geometry: GeoJSON, mine_type: "minefield"|"at_minefield"}
               Creates a minefield after build_ticks
  - construct: {type: "construct", object_type: str, geometry: GeoJSON}
               Builds a fortification/structure after build_ticks
  - deploy_bridge: {type: "deploy_bridge", target_location: {lat, lon}}
               AVLB deploys a bridge_structure

Called once per tick after movement, before detection.
"""

from __future__ import annotations

import math
import uuid

from geoalchemy2.shape import to_shape, from_shape
from shapely.geometry import Point, shape as shapely_shape

from backend.engine.map_objects import MAP_OBJECT_DEFS, get_category
from backend.models.map_object import MapObject, ObjectCategory, ObjectSide

METERS_PER_DEG_LAT = 111_320.0
METERS_PER_DEG_LON_AT_48 = 74_000.0

# Engineering unit types that can perform engineering tasks
ENGINEER_UNIT_TYPES = {
    "combat_engineer_platoon", "combat_engineer_section", "combat_engineer_team",
    "mine_layer_section", "mine_layer_team",
    "obstacle_breacher_team", "obstacle_breacher_section",
    "engineer_recon_team",
    "construction_engineer_platoon", "construction_engineer_section",
    "avlb_vehicle", "avlb_section",
    "engineer_platoon", "engineer_section",  # legacy types can also do basic engineering
}

# Which types can do which tasks
BREACH_CAPABLE = {
    "combat_engineer_platoon", "combat_engineer_section", "combat_engineer_team",
    "obstacle_breacher_team", "obstacle_breacher_section",
    "engineer_platoon", "engineer_section",
    "avlb_vehicle", "avlb_section",
}

MINE_CLEAR_CAPABLE = {
    "combat_engineer_platoon", "combat_engineer_section", "combat_engineer_team",
    "obstacle_breacher_team", "obstacle_breacher_section",
    "engineer_platoon", "engineer_section",
}

MINE_LAY_CAPABLE = {
    "mine_layer_section", "mine_layer_team",
    "combat_engineer_platoon", "combat_engineer_section",
}

CONSTRUCT_CAPABLE = {
    "construction_engineer_platoon", "construction_engineer_section",
    "combat_engineer_platoon", "combat_engineer_section",
    "engineer_platoon", "engineer_section",
}

BRIDGE_CAPABLE = {
    "avlb_vehicle", "avlb_section",
    "construction_engineer_platoon",
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
        return pt.y, pt.x
    except Exception:
        return None


def process_engineering(
    all_units: list,
    map_objects: list,
    session_id: uuid.UUID,
    new_objects_out: list,
) -> list[dict]:
    """
    Process engineering tasks for all units.

    Args:
        all_units: all Unit ORM objects
        map_objects: all MapObject ORM objects for the session
        session_id: current session UUID
        new_objects_out: list to append newly created MapObject instances

    Returns:
        list of event dicts
    """
    events = []
    objects_by_id = {str(obj.id): obj for obj in map_objects}

    for unit in all_units:
        if unit.is_destroyed:
            continue

        task = unit.current_task
        if not task:
            continue

        task_type = task.get("type", "")

        if task_type == "breach":
            evts = _process_breach(unit, task, objects_by_id)
            events.extend(evts)

        elif task_type == "lay_mines":
            evts = _process_lay_mines(unit, task, session_id, new_objects_out)
            events.extend(evts)

        elif task_type == "construct":
            evts = _process_construct(unit, task, session_id, new_objects_out)
            events.extend(evts)

        elif task_type == "deploy_bridge":
            evts = _process_deploy_bridge(unit, task, session_id, new_objects_out)
            events.extend(evts)

    return events


def _process_breach(unit, task: dict, objects_by_id: dict) -> list[dict]:
    """Process obstacle breaching — reduce breach_progress each tick."""
    events = []
    target_id = task.get("target_object_id")
    if not target_id:
        unit.current_task = None
        return events

    obj = objects_by_id.get(str(target_id))
    if not obj or not obj.is_active:
        unit.current_task = None
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} breach target no longer exists",
            "payload": {"action": "breach_target_gone"},
        })
        return events

    # Check unit is engineer-capable
    if unit.unit_type not in BREACH_CAPABLE and unit.unit_type not in MINE_CLEAR_CAPABLE:
        unit.current_task = None
        return events

    defn = MAP_OBJECT_DEFS.get(obj.object_type, {})
    breach_total = defn.get("breach_ticks", 5)
    if breach_total <= 0:
        unit.current_task = None
        return events

    # Check proximity (must be within 100m)
    unit_pos = _get_position(unit)
    if unit_pos is None:
        return events

    obj_pos = None
    if obj.geometry:
        try:
            s = to_shape(obj.geometry)
            c = s.centroid
            obj_pos = (c.y, c.x)
        except Exception:
            pass

    if obj_pos and _distance_m(unit_pos[0], unit_pos[1], obj_pos[0], obj_pos[1]) > 150:
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} too far from obstacle to breach",
            "payload": {"action": "breach_too_far"},
        })
        return events

    # Progress breach
    props = dict(obj.properties) if obj.properties else {}
    progress = props.get("breach_progress", 0.0)
    increment = 1.0 / max(1, breach_total)

    # Suppression slows breaching
    suppression = unit.suppression or 0.0
    increment *= max(0.2, 1.0 - suppression * 0.8)

    progress += increment
    props["breach_progress"] = min(1.0, progress)
    obj.properties = props

    if progress >= 1.0:
        # Breach complete!
        obj.is_active = False
        obj.health = 0.0
        unit.current_task = None
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} breached {obj.label or obj.object_type}!",
            "payload": {
                "action": "breach_complete",
                "object_id": str(obj.id),
                "object_type": obj.object_type,
            },
        })
    else:
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} breaching {obj.label or obj.object_type} ({progress*100:.0f}%)",
            "payload": {
                "action": "breach_progress",
                "object_id": str(obj.id),
                "progress": round(progress, 3),
            },
        })

    return events


def _process_lay_mines(unit, task: dict, session_id, new_objects_out: list) -> list[dict]:
    """Process mine laying — creates minefield after build_ticks."""
    events = []

    if unit.unit_type not in MINE_LAY_CAPABLE:
        unit.current_task = None
        return events

    mine_type = task.get("mine_type", "minefield")
    geojson = task.get("geometry")
    if not geojson:
        unit.current_task = None
        return events

    defn = MAP_OBJECT_DEFS.get(mine_type, MAP_OBJECT_DEFS["minefield"])
    build_total = defn.get("build_ticks", 6)

    progress = task.get("build_progress", 0.0)
    increment = 1.0 / max(1, build_total)
    suppression = unit.suppression or 0.0
    increment *= max(0.2, 1.0 - suppression * 0.8)
    progress += increment

    if progress >= 1.0:
        # Create the minefield
        try:
            geom_shape = shapely_shape(geojson)
            new_obj = MapObject(
                session_id=session_id,
                side=unit.side,
                object_type=mine_type,
                object_category=ObjectCategory.obstacle,
                geometry=from_shape(geom_shape, srid=4326),
                properties={"laid_by": str(unit.id)},
                label=f"Minefield ({unit.name})",
                is_active=True,
                health=1.0,
            )
            new_objects_out.append(new_obj)
            unit.current_task = None
            events.append({
                "event_type": "engineering",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} completed laying {mine_type}",
                "payload": {"action": "mines_laid", "mine_type": mine_type},
            })
        except Exception:
            unit.current_task = None
    else:
        task["build_progress"] = progress
        unit.current_task = task
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} laying mines ({progress*100:.0f}%)",
            "payload": {"action": "lay_mines_progress", "progress": round(progress, 3)},
        })

    return events


def _process_construct(unit, task: dict, session_id, new_objects_out: list) -> list[dict]:
    """Process construction — builds fortification/structure after build_ticks."""
    events = []

    if unit.unit_type not in CONSTRUCT_CAPABLE:
        unit.current_task = None
        return events

    obj_type = task.get("object_type", "entrenchment")
    geojson = task.get("geometry")
    if not geojson:
        unit.current_task = None
        return events

    defn = MAP_OBJECT_DEFS.get(obj_type, {})
    build_total = defn.get("build_ticks", 4)
    category = get_category(obj_type)

    progress = task.get("build_progress", 0.0)
    increment = 1.0 / max(1, build_total)
    suppression = unit.suppression or 0.0
    increment *= max(0.2, 1.0 - suppression * 0.8)
    progress += increment

    if progress >= 1.0:
        try:
            geom_shape = shapely_shape(geojson)
            new_obj = MapObject(
                session_id=session_id,
                side=unit.side,
                object_type=obj_type,
                object_category=ObjectCategory(category),
                geometry=from_shape(geom_shape, srid=4326),
                properties={"built_by": str(unit.id)},
                label=f"{obj_type.replace('_', ' ').title()} ({unit.name})",
                is_active=True,
                health=1.0,
            )
            new_objects_out.append(new_obj)
            unit.current_task = None
            events.append({
                "event_type": "engineering",
                "actor_unit_id": unit.id,
                "text_summary": f"{unit.name} completed building {obj_type.replace('_', ' ')}",
                "payload": {"action": "construct_complete", "object_type": obj_type},
            })
        except Exception:
            unit.current_task = None
    else:
        task["build_progress"] = progress
        unit.current_task = task
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} constructing {obj_type.replace('_', ' ')} ({progress*100:.0f}%)",
            "payload": {"action": "construct_progress", "progress": round(progress, 3)},
        })

    return events


def _process_deploy_bridge(unit, task: dict, session_id, new_objects_out: list) -> list[dict]:
    """AVLB deploys a bridge at target location — instant (1 tick)."""
    events = []

    if unit.unit_type not in BRIDGE_CAPABLE:
        unit.current_task = None
        return events

    target = task.get("target_location")
    if not target:
        unit.current_task = None
        return events

    lat = target.get("lat")
    lon = target.get("lon")
    if lat is None or lon is None:
        unit.current_task = None
        return events

    # Check proximity
    unit_pos = _get_position(unit)
    if unit_pos and _distance_m(unit_pos[0], unit_pos[1], lat, lon) > 200:
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} too far to deploy bridge",
            "payload": {"action": "bridge_too_far"},
        })
        return events

    progress = task.get("build_progress", 0.0)
    progress += 0.5  # 2 ticks to deploy

    if progress >= 1.0:
        new_obj = MapObject(
            session_id=session_id,
            side=unit.side,
            object_type="bridge_structure",
            object_category=ObjectCategory.structure,
            geometry=from_shape(Point(lon, lat), srid=4326),
            properties={"deployed_by": str(unit.id)},
            label=f"Bridge ({unit.name})",
            is_active=True,
            health=1.0,
        )
        new_objects_out.append(new_obj)
        unit.current_task = None
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} deployed bridge",
            "payload": {"action": "bridge_deployed", "lat": lat, "lon": lon},
        })
    else:
        task["build_progress"] = progress
        unit.current_task = task
        events.append({
            "event_type": "engineering",
            "actor_unit_id": unit.id,
            "text_summary": f"{unit.name} deploying bridge ({progress*100:.0f}%)",
            "payload": {"action": "bridge_progress", "progress": round(progress, 3)},
        })

    return events

