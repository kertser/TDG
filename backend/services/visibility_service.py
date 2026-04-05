"""
Fog-of-war visibility service.

Filters world state → per-side visible view.
- Own-side units: always fully visible
- Opposing units: only visible if within detection range of own-side unit
- Admin/observer: sees everything
"""

from __future__ import annotations

import uuid

from geoalchemy2.shape import to_shape
from sqlalchemy import select, func, cast
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2 import Geography

from backend.models.unit import Unit
from backend.models.contact import Contact


def _serialize_unit(unit: Unit) -> dict:
    """Serialize a Unit ORM object to a dict with lat/lon extracted from PostGIS."""
    lat, lon = None, None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            lon, lat = pt.x, pt.y
        except Exception:
            pass

    return {
        "id": str(unit.id),
        "session_id": str(unit.session_id),
        "side": unit.side.value,
        "name": unit.name,
        "unit_type": unit.unit_type,
        "sidc": unit.sidc,
        "lat": lat,
        "lon": lon,
        "heading_deg": unit.heading_deg,
        "strength": unit.strength,
        "ammo": unit.ammo,
        "morale": unit.morale,
        "suppression": unit.suppression,
        "comms_status": unit.comms_status.value if unit.comms_status else "operational",
        "current_task": unit.current_task,
        "move_speed_mps": unit.move_speed_mps,
        "detection_range_m": unit.detection_range_m,
        "is_destroyed": unit.is_destroyed,
        "assigned_user_ids": unit.assigned_user_ids,
    }


def _serialize_contact(contact: Contact) -> dict:
    """Serialize a Contact ORM object to a dict with lat/lon."""
    lat, lon = None, None
    if contact.location_estimate is not None:
        try:
            pt = to_shape(contact.location_estimate)
            lon, lat = pt.x, pt.y
        except Exception:
            pass

    return {
        "id": str(contact.id),
        "session_id": str(contact.session_id),
        "observing_side": contact.observing_side.value,
        "estimated_type": contact.estimated_type,
        "estimated_size": contact.estimated_size,
        "lat": lat,
        "lon": lon,
        "location_accuracy_m": contact.location_accuracy_m,
        "confidence": contact.confidence,
        "last_seen_tick": contact.last_seen_tick,
        "source": contact.source,
        "is_stale": contact.is_stale,
    }


async def get_visible_units(
    session_id: uuid.UUID,
    side: str,
    db: AsyncSession,
) -> list[dict]:
    """
    Return units visible to the given side.

    - Own-side units: always visible (full detail)
    - Admin/observer: all units visible
    - Opposing units: only if within detection range of any friendly unit
      (fog-of-war via PostGIS ST_DWithin)
    """
    if side in ("admin", "observer"):
        # See everything
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.is_destroyed == False,
            )
        )
        return [_serialize_unit(u) for u in result.scalars().all()]

    # Own-side units — always visible
    own_result = await db.execute(
        select(Unit).where(
            Unit.session_id == session_id,
            Unit.side == side,
            Unit.is_destroyed == False,
        )
    )
    own_units = own_result.scalars().all()

    # Opposing side
    opposing_side = "red" if side == "blue" else "blue"

    # Fog-of-war: find enemy units within detection range of any own unit
    # Using PostGIS ST_DWithin with geography cast for meter-based distance
    # Subquery: all own unit positions with their detection ranges
    own_observer = (
        select(
            Unit.position.label("obs_pos"),
            Unit.detection_range_m.label("det_range"),
        )
        .where(
            Unit.session_id == session_id,
            Unit.side == side,
            Unit.is_destroyed == False,
            Unit.position.isnot(None),
        )
        .subquery()
    )

    # Find opposing units within detection range of ANY observer
    enemy_query = (
        select(Unit)
        .where(
            Unit.session_id == session_id,
            Unit.side == opposing_side,
            Unit.is_destroyed == False,
            Unit.position.isnot(None),
        )
        .where(
            Unit.id.in_(
                select(Unit.id)
                .where(
                    Unit.session_id == session_id,
                    Unit.side == opposing_side,
                    Unit.is_destroyed == False,
                    Unit.position.isnot(None),
                )
                .where(
                    func.ST_DWithin(
                        cast(Unit.position, Geography),
                        cast(own_observer.c.obs_pos, Geography),
                        own_observer.c.det_range,
                    )
                )
            )
        )
    )

    try:
        enemy_result = await db.execute(enemy_query)
        enemy_units = enemy_result.scalars().all()
    except Exception:
        # If the spatial query fails (e.g., no PostGIS, no positions),
        # fall back to no enemy visibility
        enemy_units = []

    all_visible = [_serialize_unit(u) for u in own_units]
    for u in enemy_units:
        serialized = _serialize_unit(u)
        # Reduce detail for detected enemy units (fog-of-war: partial info)
        serialized["ammo"] = None
        serialized["morale"] = None
        serialized["suppression"] = None
        serialized["comms_status"] = None
        serialized["current_task"] = None
        serialized["move_speed_mps"] = None
        all_visible.append(serialized)

    return all_visible


async def get_visible_contacts(
    session_id: uuid.UUID,
    side: str,
    db: AsyncSession,
) -> list[dict]:
    """Return contacts visible to the given side."""
    if side in ("admin", "observer"):
        result = await db.execute(
            select(Contact).where(Contact.session_id == session_id)
        )
    else:
        result = await db.execute(
            select(Contact).where(
                Contact.session_id == session_id,
                Contact.observing_side == side,
            )
        )
    return [_serialize_contact(c) for c in result.scalars().all()]

