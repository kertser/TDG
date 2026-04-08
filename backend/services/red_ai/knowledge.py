"""
Red AI Knowledge State Builder — constructs what a Red commander knows.

CRITICAL: This module ONLY queries Red-side data. It NEVER accesses
Blue unit positions, Blue orders, or any other Blue-only information.
Red AI sees ONLY:
  - Its own units (full state)
  - Contacts detected by Red (via Red's detection)
  - Terrain and map objects discovered by Red
  - Its own orders and mission intent
"""

from __future__ import annotations

import uuid
import math
from typing import Any

from geoalchemy2.shape import to_shape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.unit import Unit
from backend.models.contact import Contact
from backend.models.map_object import MapObject
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell


async def build_knowledge_state(
    session_id: uuid.UUID,
    controlled_unit_ids: list[uuid.UUID] | None,
    db: AsyncSession,
    grid_service: Any = None,
) -> dict[str, Any]:
    """
    Build the knowledge state for a Red AI agent.

    Returns a dict with:
    - own_units: list of Red unit dicts (full info + grid_ref)
    - known_contacts: list of Red-side contacts (enemy detections + grid_ref)
    - terrain_around_units: terrain type at each unit's position
    - discovered_objects: map objects visible to Red
    - summary: aggregate stats
    """
    knowledge: dict[str, Any] = {}

    # ── Load grid service if not provided ─────────────────
    if grid_service is None:
        try:
            from backend.models.grid import GridDefinition
            from backend.services.grid_service import GridService
            gd_result = await db.execute(
                select(GridDefinition).where(GridDefinition.session_id == session_id)
            )
            gd = gd_result.scalar_one_or_none()
            if gd:
                grid_service = GridService(gd)
        except Exception:
            pass

    # ── Own units (Red side only) ─────────────────────────
    if controlled_unit_ids:
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.id.in_(controlled_unit_ids),
                Unit.is_destroyed == False,
            )
        )
    else:
        # If no specific controlled units, get all Red units
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.side == "red",
                Unit.is_destroyed == False,
            )
        )
    units = result.scalars().all()

    own_units = []
    for u in units:
        lat, lon = None, None
        if u.position:
            try:
                pt = to_shape(u.position)
                lat, lon = pt.y, pt.x
            except Exception:
                pass

        unit_dict = {
            "id": str(u.id),
            "name": u.name,
            "unit_type": u.unit_type,
            "strength": round(u.strength or 1.0, 2),
            "ammo": round(u.ammo or 1.0, 2),
            "morale": round(u.morale or 1.0, 2),
            "suppression": round(u.suppression or 0.0, 2),
            "comms_status": u.comms_status.value if hasattr(u.comms_status, 'value') else (u.comms_status or "operational"),
            "current_task": u.current_task,
            "lat": lat,
            "lon": lon,
            "heading_deg": u.heading_deg,
            "detection_range_m": u.detection_range_m or 1500.0,
            "move_speed_mps": u.move_speed_mps,
            "capabilities": u.capabilities,
        }

        # Add grid reference if grid service is available
        if grid_service and lat is not None and lon is not None:
            try:
                unit_dict["grid_ref"] = grid_service.point_to_snail(lat, lon, depth=2)
            except Exception:
                pass

        own_units.append(unit_dict)
    knowledge["own_units"] = own_units

    # ── Known contacts (Red side detections ONLY) ─────────
    contacts_result = await db.execute(
        select(Contact).where(
            Contact.session_id == session_id,
            Contact.observing_side == "red",
            Contact.is_stale == False,
        ).limit(20)
    )
    contacts = contacts_result.scalars().all()

    known_contacts = []
    for c in contacts:
        c_lat, c_lon = None, None
        if c.location_estimate:
            try:
                pt = to_shape(c.location_estimate)
                c_lat, c_lon = pt.y, pt.x
            except Exception:
                pass

        contact_info = {
            "estimated_type": c.estimated_type or "unknown",
            "estimated_size": c.estimated_size,
            "confidence": round(c.confidence, 2),
            "source": c.source,
            "lat": c_lat,
            "lon": c_lon,
            "last_seen_tick": c.last_seen_tick,
        }

        # Add grid reference for contact
        if grid_service and c_lat is not None and c_lon is not None:
            try:
                contact_info["grid_ref"] = grid_service.point_to_snail(c_lat, c_lon, depth=2)
            except Exception:
                pass

        # Calculate distance and bearing from nearest own unit
        if c_lat and c_lon and own_units:
            nearest_dist = float("inf")
            nearest_bearing = None
            for u in own_units:
                if u["lat"] and u["lon"]:
                    dist = _approx_distance_m(u["lat"], u["lon"], c_lat, c_lon)
                    if dist < nearest_dist:
                        nearest_dist = dist
                        nearest_bearing = _bearing_deg(u["lat"], u["lon"], c_lat, c_lon)
            contact_info["distance_to_nearest_m"] = round(nearest_dist)
            if nearest_bearing is not None:
                contact_info["bearing_from_nearest_deg"] = round(nearest_bearing)

        known_contacts.append(contact_info)

    knowledge["known_contacts"] = known_contacts

    # ── Discovered map objects (Red only) ─────────────────
    try:
        obj_result = await db.execute(
            select(MapObject).where(
                MapObject.session_id == session_id,
                MapObject.is_active == True,
                MapObject.discovered_by_red == True,
            ).limit(30)
        )
        objects = obj_result.scalars().all()
        discovered = []
        for obj in objects:
            obj_info = {
                "type": obj.object_type,
                "category": obj.object_category.value if hasattr(obj.object_category, 'value') else obj.object_category,
                "label": obj.label,
            }
            # Get object position
            if obj.geometry:
                try:
                    shape = to_shape(obj.geometry)
                    centroid = shape.centroid
                    obj_info["lat"] = round(centroid.y, 6)
                    obj_info["lon"] = round(centroid.x, 6)
                    if grid_service:
                        obj_info["grid_ref"] = grid_service.point_to_snail(
                            centroid.y, centroid.x, depth=2
                        )
                except Exception:
                    pass
            discovered.append(obj_info)
        knowledge["discovered_objects"] = discovered
    except Exception:
        knowledge["discovered_objects"] = []

    # ── Terrain at each unit's position ───────────────────
    # Query terrain cells near our units (using snail_path match)
    terrain_around = {}
    unit_snail_paths = []
    for u in own_units:
        grid_ref = u.get("grid_ref")
        if grid_ref:
            unit_snail_paths.append(grid_ref)
            # Also query parent path for broader terrain picture
            if "-" in grid_ref:
                parent = grid_ref.rsplit("-", 1)[0]
                unit_snail_paths.append(parent)

    if unit_snail_paths:
        try:
            # Query terrain cells at unit positions
            tc_result = await db.execute(
                select(TerrainCell.snail_path, TerrainCell.terrain_type,
                       TerrainCell.elevation_m, TerrainCell.slope_deg).where(
                    TerrainCell.session_id == session_id,
                    TerrainCell.snail_path.in_(unit_snail_paths),
                )
            )
            for row in tc_result.all():
                cell_info = {"terrain_type": row[1]}
                if row[2] is not None:
                    cell_info["elevation_m"] = round(row[2], 1)
                if row[3] is not None:
                    cell_info["slope_deg"] = round(row[3], 1)
                terrain_around[row[0]] = cell_info
        except Exception:
            pass

    knowledge["terrain_around_units"] = terrain_around
    knowledge["terrain_types_present"] = list(set(
        v["terrain_type"] for v in terrain_around.values()
    ))

    # ── Elevation at unit positions ────────────────────────
    if unit_snail_paths:
        try:
            ec_result = await db.execute(
                select(ElevationCell.snail_path, ElevationCell.elevation_m,
                       ElevationCell.slope_deg).where(
                    ElevationCell.session_id == session_id,
                    ElevationCell.snail_path.in_(unit_snail_paths),
                )
            )
            elevation_data = {}
            for row in ec_result.all():
                elevation_data[row[0]] = {
                    "elevation_m": round(row[1], 1) if row[1] is not None else None,
                    "slope_deg": round(row[2], 1) if row[2] is not None else None,
                }
            knowledge["elevation_at_units"] = elevation_data
        except Exception:
            knowledge["elevation_at_units"] = {}

    # ── Summary stats ─────────────────────────────────────
    units_with_tasks = [u for u in own_units if u.get("current_task")]
    knowledge["summary"] = {
        "total_units": len(own_units),
        "avg_strength": round(sum(u["strength"] for u in own_units) / len(own_units), 2) if own_units else 0,
        "avg_morale": round(sum(u["morale"] for u in own_units) / len(own_units), 2) if own_units else 0,
        "avg_ammo": round(sum(u["ammo"] for u in own_units) / len(own_units), 2) if own_units else 0,
        "total_contacts": len(known_contacts),
        "units_idle": len(own_units) - len(units_with_tasks),
        "units_moving": sum(
            1 for u in units_with_tasks
            if u["current_task"] and u["current_task"].get("type") == "move"
        ),
        "units_attacking": sum(
            1 for u in units_with_tasks
            if u["current_task"] and u["current_task"].get("type") in ("attack", "engage")
        ),
        "units_defending": sum(
            1 for u in units_with_tasks
            if u["current_task"] and u["current_task"].get("type") in ("defend", "hold")
        ),
    }

    return knowledge


def _approx_distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate distance in meters (flat-earth, good for short distances)."""
    METERS_PER_DEG_LAT = 111_320.0
    METERS_PER_DEG_LON_AT_48 = 74_000.0
    dlat = (lat2 - lat1) * METERS_PER_DEG_LAT
    dlon = (lon2 - lon1) * METERS_PER_DEG_LON_AT_48
    return math.sqrt(dlat * dlat + dlon * dlon)


def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Approximate bearing in degrees from point 1 to point 2."""
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    bearing = math.degrees(math.atan2(dlon, dlat)) % 360
    return bearing


