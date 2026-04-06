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

from sqlalchemy.orm import selectinload

from backend.models.unit import Unit
from backend.models.contact import Contact
from backend.models.session import SessionParticipant


def _serialize_unit(unit: Unit) -> dict:
    """Serialize a Unit ORM object to a dict with lat/lon extracted from PostGIS."""
    lat, lon = None, None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            lon, lat = pt.x, pt.y
        except Exception:
            pass

    # Compute unit status from current_task
    status = _compute_unit_status(unit)

    return {
        "id": str(unit.id),
        "session_id": str(unit.session_id),
        "side": unit.side.value,
        "name": unit.name,
        "unit_type": unit.unit_type,
        "sidc": unit.sidc,
        "parent_unit_id": str(unit.parent_unit_id) if unit.parent_unit_id else None,
        "lat": lat,
        "lon": lon,
        "heading_deg": unit.heading_deg,
        "strength": unit.strength,
        "ammo": unit.ammo,
        "morale": unit.morale,
        "suppression": unit.suppression,
        "comms_status": unit.comms_status.value if unit.comms_status else "operational",
        "current_task": unit.current_task,
        "capabilities": unit.capabilities,
        "move_speed_mps": unit.move_speed_mps,
        "detection_range_m": unit.detection_range_m,
        "is_destroyed": unit.is_destroyed,
        "assigned_user_ids": unit.assigned_user_ids,
        "unit_status": status,
    }


def _compute_unit_status(unit: Unit) -> str:
    """Derive a human-readable status string from unit state."""
    if unit.is_destroyed:
        return "destroyed"
    if unit.strength is not None and unit.strength <= 0:
        return "destroyed"
    if unit.morale is not None and unit.morale < 0.15:
        return "broken"
    if unit.suppression is not None and unit.suppression > 0.7:
        return "suppressed"

    task = unit.current_task
    if task:
        task_type = task.get("type", "")
        if task_type in ("attack", "engage"):
            return "engaging"
        if task_type in ("move", "advance"):
            return "moving"
        if task_type in ("retreat", "withdraw"):
            return "retreating"
        if task_type in ("defend", "hold"):
            return "defending"
        if task_type in ("observe", "recon"):
            return "observing"
        if task_type in ("support",):
            return "supporting"
        if task_type:
            return task_type  # custom status

    return "idle"


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

    # Normalize side to only allow blue/red for fog-of-war
    if side not in ("blue", "red"):
        # Unknown side — show nothing to prevent data leaks
        return []

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


async def _get_user_map(session_id: uuid.UUID, db: AsyncSession) -> dict[str, str]:
    """Return a mapping of user_id (str) → display_name for session participants."""
    result = await db.execute(
        select(SessionParticipant)
        .options(selectinload(SessionParticipant.user))
        .where(SessionParticipant.session_id == session_id)
    )
    participants = result.scalars().all()
    return {
        str(p.user_id): p.user.display_name
        for p in participants
        if p.user
    }


async def enrich_units_with_command_info(
    units_data: list[dict],
    session_id: uuid.UUID,
    db: AsyncSession,
    requesting_side: str | None = None,
) -> list[dict]:
    """
    Add commanding_user_name and assigned_user_names to each unit dict.

    For each unit, the commanding user is determined by walking UP the parent
    chain (including self) and finding the first unit with an assigned user.
    This mirrors real military Chain of Command.
    """
    user_map = await _get_user_map(session_id, db)

    # Build unit lookup by ID for parent-chain walking
    unit_lookup = {u["id"]: u for u in units_data}

    for u in units_data:
        # For enemy units behind fog-of-war, don't expose command structure
        if (
            requesting_side
            and requesting_side not in ("admin", "observer")
            and u.get("side") != requesting_side
        ):
            u["assigned_user_names"] = []
            u["commanding_user_name"] = None
            continue

        # Resolve assigned user IDs → display names
        names = []
        if u.get("assigned_user_ids"):
            for uid in u["assigned_user_ids"]:
                name = user_map.get(uid)
                if name:
                    names.append(name)
        u["assigned_user_names"] = names

        # Walk up parent chain to find commanding user (first assigned user)
        cmd_name = None
        current = u
        visited = set()
        while current:
            cid = current["id"]
            if cid in visited:
                break
            visited.add(cid)

            if current.get("assigned_user_ids"):
                for uid in current["assigned_user_ids"]:
                    name = user_map.get(uid)
                    if name:
                        cmd_name = name
                        break
                if cmd_name:
                    break

            parent_id = current.get("parent_unit_id")
            if parent_id and parent_id in unit_lookup:
                current = unit_lookup[parent_id]
            else:
                break
        u["commanding_user_name"] = cmd_name

    return units_data


async def check_command_authority(
    user_id: str,
    unit: Unit,
    session_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """
    Check if a user has command authority over a unit via the hierarchy.

    A user has authority if they are assigned to the unit itself
    OR to any proper ancestor in the parent chain.
    """
    # Check self first
    if unit.assigned_user_ids and user_id in unit.assigned_user_ids:
        return True

    # Load lightweight unit data for parent-chain walk
    result = await db.execute(
        select(Unit.id, Unit.parent_unit_id, Unit.assigned_user_ids).where(
            Unit.session_id == session_id, Unit.is_destroyed == False
        )
    )
    rows = result.all()
    unit_info = {
        str(r[0]): (str(r[1]) if r[1] else None, r[2])
        for r in rows
    }

    # Walk up parent chain
    current_parent_id = str(unit.parent_unit_id) if unit.parent_unit_id else None
    visited = set()
    while current_parent_id and current_parent_id not in visited:
        visited.add(current_parent_id)
        info = unit_info.get(current_parent_id)
        if info is None:
            break
        parent_parent_id, assigned_ids = info
        if assigned_ids and user_id in assigned_ids:
            return True
        current_parent_id = parent_parent_id

    return False


