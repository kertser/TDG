"""Map Objects API – CRUD for tactical obstacles and structures, plus engineering actions."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from geoalchemy2.shape import from_shape, to_shape
from pydantic import BaseModel
from shapely.geometry import shape as shapely_shape, mapping as shapely_mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.map_object import MapObject, ObjectCategory, ObjectSide
from backend.models.unit import Unit
from backend.engine.map_objects import MAP_OBJECT_DEFS, OBSTACLE_TYPES, STRUCTURE_TYPES, ALL_OBJECT_TYPES, get_category
from backend.services.ws_manager import ws_manager

router = APIRouter()


# ── Request / Response models ────────────────────────

class MapObjectCreate(BaseModel):
    object_type: str
    side: str = "neutral"
    geometry: dict | None = None  # GeoJSON geometry
    label: str | None = None
    properties: dict | None = None
    style_json: dict | None = None


class MapObjectUpdate(BaseModel):
    geometry: dict | None = None
    label: str | None = None
    properties: dict | None = None
    style_json: dict | None = None
    is_active: bool | None = None
    health: float | None = None
    side: str | None = None


class EngineerActionRequest(BaseModel):
    action: str  # "breach", "lay_mines", "construct", "deploy_bridge"
    target_object_id: str | None = None  # for breach
    object_type: str | None = None  # for construct/lay_mines
    geometry: dict | None = None  # for lay_mines/construct
    target_lat: float | None = None  # for deploy_bridge
    target_lon: float | None = None


def _serialize_map_object(obj: MapObject) -> dict:
    """Serialize a MapObject to a dict with GeoJSON geometry."""
    geojson = None
    if obj.geometry is not None:
        try:
            shape = to_shape(obj.geometry)
            geojson = shapely_mapping(shape)
        except Exception:
            pass

    defn = MAP_OBJECT_DEFS.get(obj.object_type, {})

    return {
        "id": str(obj.id),
        "session_id": str(obj.session_id),
        "side": obj.side.value if hasattr(obj.side, 'value') else str(obj.side),
        "object_type": obj.object_type,
        "object_category": obj.object_category.value if hasattr(obj.object_category, 'value') else str(obj.object_category),
        "geometry": geojson,
        "label": obj.label,
        "properties": obj.properties,
        "style_json": obj.style_json,
        "is_active": obj.is_active,
        "health": obj.health,
        "created_at": obj.created_at.isoformat() if obj.created_at else None,
        "updated_at": obj.updated_at.isoformat() if obj.updated_at else None,
        # Include definition info for frontend
        "definition": {
            "description": defn.get("description", ""),
            "geometry_type": defn.get("geometry_type", "Point"),
            "effect_radius_m": defn.get("effect_radius_m", 0),
            "color": defn.get("color", "#888888"),
            "dash_pattern": defn.get("dash_pattern"),
            "protection_bonus": defn.get("protection_bonus", 1.0),
            "breach_ticks": defn.get("breach_ticks", 0),
        },
    }


# ── Endpoints ────────────────────────────────────────

@router.get("/{session_id}/map-objects/definitions")
async def get_object_definitions():
    """Return all map object type definitions for the frontend."""
    result = {}
    for key, defn in MAP_OBJECT_DEFS.items():
        result[key] = {
            "category": defn["category"],
            "geometry_type": defn["geometry_type"],
            "description": defn.get("description", ""),
            "color": defn.get("color", "#888888"),
            "dash_pattern": defn.get("dash_pattern"),
            "effect_radius_m": defn.get("effect_radius_m", 0),
            "protection_bonus": defn.get("protection_bonus", 1.0),
            "detection_bonus_m": defn.get("detection_bonus_m", 0),
            "breach_ticks": defn.get("breach_ticks", 0),
            "build_ticks": defn.get("build_ticks", 0),
            "vehicle_passable": defn.get("vehicle_passable", True),
            "infantry_passable": defn.get("infantry_passable", True),
            "damage_per_tick": defn.get("damage_per_tick", 0),
        }
    return {
        "definitions": result,
        "obstacle_types": OBSTACLE_TYPES,
        "structure_types": STRUCTURE_TYPES,
    }


@router.get("/{session_id}/map-objects")
async def get_map_objects(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return all map objects for a session."""
    result = await db.execute(
        select(MapObject).where(MapObject.session_id == session_id)
    )
    objects = result.scalars().all()
    return [_serialize_map_object(obj) for obj in objects]


@router.post("/{session_id}/map-objects")
async def create_map_object(
    session_id: uuid.UUID,
    body: MapObjectCreate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Create a new map object (admin only in practice, but any participant can call)."""
    if body.object_type not in ALL_OBJECT_TYPES:
        raise HTTPException(status_code=400, detail=f"Unknown object type: {body.object_type}. Valid: {ALL_OBJECT_TYPES}")

    category = get_category(body.object_type)

    # Parse geometry
    geom = None
    if body.geometry:
        try:
            shape = shapely_shape(body.geometry)
            geom = from_shape(shape, srid=4326)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid geometry: {e}")

    # Map side string to enum
    try:
        side_enum = ObjectSide(body.side)
    except ValueError:
        side_enum = ObjectSide.neutral

    obj = MapObject(
        session_id=session_id,
        side=side_enum,
        object_type=body.object_type,
        object_category=ObjectCategory(category),
        geometry=geom,
        label=body.label,
        properties=body.properties,
        style_json=body.style_json,
        is_active=True,
        health=1.0,
        placed_by_user_id=participant.user_id,
    )
    db.add(obj)
    await db.flush()
    await db.refresh(obj)
    await db.commit()

    serialized = _serialize_map_object(obj)

    # Broadcast to all clients in the session
    await ws_manager.broadcast(session_id, {
        "type": "map_object_created",
        "data": serialized,
    })

    return serialized


@router.put("/{session_id}/map-objects/{object_id}")
async def update_map_object(
    session_id: uuid.UUID,
    object_id: uuid.UUID,
    body: MapObjectUpdate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Update a map object."""
    result = await db.execute(
        select(MapObject).where(MapObject.id == object_id, MapObject.session_id == session_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Map object not found")

    if body.geometry is not None:
        try:
            shape = shapely_shape(body.geometry)
            obj.geometry = from_shape(shape, srid=4326)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid geometry: {e}")

    if body.label is not None:
        obj.label = body.label
    if body.properties is not None:
        obj.properties = body.properties
    if body.style_json is not None:
        obj.style_json = body.style_json
    if body.is_active is not None:
        obj.is_active = body.is_active
    if body.health is not None:
        obj.health = max(0.0, min(1.0, body.health))
    if body.side is not None:
        try:
            obj.side = ObjectSide(body.side)
        except ValueError:
            pass

    await db.flush()
    await db.commit()

    serialized = _serialize_map_object(obj)

    # Broadcast to all clients in the session
    await ws_manager.broadcast(session_id, {
        "type": "map_object_updated",
        "data": serialized,
    })

    return serialized


@router.delete("/{session_id}/map-objects/{object_id}")
async def delete_map_object(
    session_id: uuid.UUID,
    object_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Delete a map object."""
    result = await db.execute(
        select(MapObject).where(MapObject.id == object_id, MapObject.session_id == session_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Map object not found")
    await db.delete(obj)
    await db.commit()

    # Broadcast deletion to all clients in the session
    await ws_manager.broadcast(session_id, {
        "type": "map_object_deleted",
        "data": {"id": str(object_id), "object_id": str(object_id)},
    })

    return {"status": "deleted", "id": str(object_id)}


@router.post("/{session_id}/map-objects/{object_id}/breach")
async def breach_object(
    session_id: uuid.UUID,
    object_id: uuid.UUID,
    unit_id: str = "",
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Assign an engineering unit to breach an obstacle."""
    if not unit_id:
        raise HTTPException(status_code=400, detail="unit_id required")

    result = await db.execute(
        select(MapObject).where(MapObject.id == object_id, MapObject.session_id == session_id)
    )
    obj = result.scalar_one_or_none()
    if obj is None:
        raise HTTPException(status_code=404, detail="Map object not found")

    try:
        uid = uuid.UUID(unit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid unit_id")

    result = await db.execute(
        select(Unit).where(Unit.id == uid, Unit.session_id == session_id, Unit.is_destroyed == False)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    unit.current_task = {
        "type": "breach",
        "target_object_id": str(object_id),
    }
    await db.flush()
    await db.commit()
    return {"status": "breach_assigned", "unit_id": str(uid), "object_id": str(object_id)}


@router.post("/{session_id}/units/{unit_id}/engineer-action")
async def engineer_action(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: EngineerActionRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Assign an engineering task to a unit (lay mines, construct, deploy bridge)."""
    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id, Unit.is_destroyed == False)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if body.action == "breach":
        if not body.target_object_id:
            raise HTTPException(status_code=400, detail="target_object_id required for breach")
        unit.current_task = {
            "type": "breach",
            "target_object_id": body.target_object_id,
        }

    elif body.action == "lay_mines":
        if not body.geometry:
            raise HTTPException(status_code=400, detail="geometry required for lay_mines")
        mine_type = body.object_type or "minefield"
        unit.current_task = {
            "type": "lay_mines",
            "geometry": body.geometry,
            "mine_type": mine_type,
            "build_progress": 0.0,
        }

    elif body.action == "construct":
        if not body.geometry or not body.object_type:
            raise HTTPException(status_code=400, detail="geometry and object_type required for construct")
        unit.current_task = {
            "type": "construct",
            "object_type": body.object_type,
            "geometry": body.geometry,
            "build_progress": 0.0,
        }

    elif body.action == "deploy_bridge":
        if body.target_lat is None or body.target_lon is None:
            raise HTTPException(status_code=400, detail="target_lat and target_lon required")
        unit.current_task = {
            "type": "deploy_bridge",
            "target_location": {"lat": body.target_lat, "lon": body.target_lon},
            "build_progress": 0.0,
        }

    else:
        raise HTTPException(status_code=400, detail=f"Unknown action: {body.action}")

    await db.flush()
    await db.commit()
    return {"status": "task_assigned", "unit_id": str(unit_id), "action": body.action}


@router.delete("/{session_id}/map-objects")
async def delete_all_map_objects(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Delete all map objects for a session (admin action)."""
    result = await db.execute(
        select(MapObject).where(MapObject.session_id == session_id)
    )
    objects = result.scalars().all()
    for obj in objects:
        await db.delete(obj)
    await db.commit()
    return {"status": "all_deleted", "count": len(objects)}

