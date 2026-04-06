"""Units API – fog-of-war filtered unit and contact retrieval, unit assignment,
hierarchy, split/merge, formation, movement commands."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.services.visibility_service import (
    get_visible_units,
    get_visible_contacts,
    enrich_units_with_command_info,
    check_command_authority,
    check_subordinate_authority,
    _serialize_unit,
)
from backend.models.unit import Unit
from backend.models.session import SessionParticipant, Side

router = APIRouter()


class UnitAssignRequest(BaseModel):
    assigned_user_ids: list[str]


class UnitRenameRequest(BaseModel):
    name: str


class UnitFormationRequest(BaseModel):
    formation: str  # column, line, wedge, vee, echelon_left, echelon_right, staggered, box, diamond, dispersed


class UnitMoveRequest(BaseModel):
    target_lat: float
    target_lon: float
    speed: str = "average"  # slow, average, fast


class UnitSplitRequest(BaseModel):
    ratio: float = 0.5  # fraction that goes to new unit (0.1–0.9)
    new_name: str | None = None


class UnitMergeRequest(BaseModel):
    merge_with_unit_id: str


@router.get("/{session_id}/units")
async def get_units(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return units visible to the requester's side (fog-of-war filtered),
    enriched with commanding officer info."""
    side = participant.side.value
    # Force fog-of-war for regular gameplay: only blue or red get filtered view.
    # Admin/observer participants should use the admin god-view endpoint for all units.
    # Here we give them the blue view by default so they don't accidentally leak intel.
    if side not in ("blue", "red"):
        side = "blue"
    units = await get_visible_units(session_id, side, db)
    # Enrich with commanding user names for popups
    units = await enrich_units_with_command_info(units, session_id, db, requesting_side=side)
    return units


@router.get("/{session_id}/units/hierarchy")
async def get_unit_hierarchy(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return unit hierarchy with command info for the user's side.
    Admin/observer sees all sides."""
    side = participant.side.value

    if side in ("admin", "observer"):
        # See all units
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.is_destroyed == False,
            )
        )
    else:
        # Own-side only
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.side == side,
                Unit.is_destroyed == False,
            )
        )
    units = result.scalars().all()
    serialized = [_serialize_unit(u) for u in units]

    # Enrich with user names and commanding officer
    enriched = await enrich_units_with_command_info(
        serialized, session_id, db, requesting_side=side,
    )
    return enriched


@router.get("/{session_id}/units/{unit_id}")
async def get_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return a single unit if visible to the requester."""
    side = participant.side.value
    units = await get_visible_units(session_id, side, db)
    for u in units:
        if u["id"] == str(unit_id):
            return u
    raise HTTPException(status_code=404, detail="Unit not found or not visible")


@router.put("/{session_id}/units/{unit_id}/rename")
async def rename_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitRenameRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Rename a unit. Requires command authority over the unit."""
    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Admin/observer can rename any unit
    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot rename units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")

    unit.name = new_name
    await db.flush()
    return {"id": str(unit.id), "name": unit.name}


@router.put("/{session_id}/units/{unit_id}/assign")
async def assign_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitAssignRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """
    Assign users to a unit.

    Permission rules:
    - Admin or observer (referee) can assign any unit.
    - A user who already owns the unit (in assigned_user_ids) can modify assignment.
    - A user who commands an ancestor of the unit (chain of command) can assign it.
    - If the unit is unassigned, a same-side participant can claim it.
    - Otherwise, a user who does not own the unit cannot assign it.
    """
    side = participant.side.value
    user_id = str(participant.user_id)

    # Fetch the unit
    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Admin / observer (referee) can assign anything
    if side in ("admin", "observer"):
        pass
    else:
        # Must be same side
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot assign units from other side")

        current_owners = unit.assigned_user_ids or []

        # Check if user has command authority via hierarchy (self or ancestor)
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)

        if not has_authority:
            # Fall back to original rules: owner or unassigned
            if len(current_owners) > 0 and user_id not in current_owners:
                raise HTTPException(
                    status_code=403,
                    detail="Only commanders, the unit owner, admin, or referee can assign this unit",
                )

    # Validate: observers cannot be assigned to units
    if body.assigned_user_ids:
        for uid_str in body.assigned_user_ids:
            try:
                uid = uuid.UUID(uid_str)
            except ValueError:
                continue
            part_result = await db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.session_id == session_id,
                    SessionParticipant.user_id == uid,
                )
            )
            part = part_result.scalar_one_or_none()
            if part and part.side == Side.observer:
                raise HTTPException(
                    status_code=400,
                    detail="Cannot assign observer to unit — observers do not control units",
                )

    unit.assigned_user_ids = body.assigned_user_ids if body.assigned_user_ids else None
    await db.flush()
    return {"id": str(unit.id), "assigned_user_ids": unit.assigned_user_ids}


# ══════════════════════════════════════════════════
# ── Formation ─────────────────────────────────────
# ══════════════════════════════════════════════════

VALID_FORMATIONS = {
    "column", "line", "wedge", "vee", "echelon_left", "echelon_right",
    "staggered", "box", "diamond", "dispersed",
}


@router.put("/{session_id}/units/{unit_id}/formation")
async def set_unit_formation(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitFormationRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Set unit formation. Requires command authority."""
    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot modify units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    if body.formation not in VALID_FORMATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid formation. Valid: {', '.join(sorted(VALID_FORMATIONS))}")

    caps = unit.capabilities or {}
    caps["formation"] = body.formation
    unit.capabilities = caps
    await db.flush()
    return {"id": str(unit.id), "formation": body.formation}


# ══════════════════════════════════════════════════
# ── Movement Command ──────────────────────────────
# ══════════════════════════════════════════════════

SPEED_VALUES = {
    "slow": 1.5,
    "average": 4.0,
    "fast": 8.0,
}


@router.put("/{session_id}/units/{unit_id}/move")
async def set_unit_move(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitMoveRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Set unit movement task with target and speed. Requires command authority or admin."""
    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot command units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    speed_label = body.speed.lower()
    if speed_label not in SPEED_VALUES:
        raise HTTPException(status_code=400, detail=f"Invalid speed. Valid: slow, average, fast")

    unit.move_speed_mps = SPEED_VALUES[speed_label]
    unit.current_task = {
        "type": "move",
        "target_location": {"lat": body.target_lat, "lon": body.target_lon},
        "speed": speed_label,
    }
    await db.flush()
    return _serialize_unit(unit)


@router.put("/{session_id}/units/{unit_id}/stop")
async def stop_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Clear unit's current task (stop movement). Requires command authority."""
    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot command units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    unit.current_task = None
    await db.flush()
    return _serialize_unit(unit)


# ══════════════════════════════════════════════════
# ── Split / Merge ─────────────────────────────────
# ══════════════════════════════════════════════════

@router.post("/{session_id}/units/{unit_id}/split")
async def split_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitSplitRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Split a unit into two units of the same type.
    The original keeps (1-ratio) of strength, the new unit gets (ratio).
    Ammo and morale are copied. Both remain under the same parent.
    Requires command authority."""
    from geoalchemy2.shape import from_shape, to_shape
    from shapely.geometry import Point as ShapelyPoint

    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot split units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    ratio = max(0.1, min(0.9, body.ratio))

    # Determine names
    base_name = unit.name
    # Auto-suffix: if the name already has /N, increment; else add /1 and /2
    if "/" in base_name:
        prefix = base_name.rsplit("/", 1)[0]
    else:
        prefix = base_name
    name_a = f"{prefix}/1"
    name_b = body.new_name or f"{prefix}/2"

    # Copy position
    position_copy = None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            # Offset the new unit slightly (50m east) to avoid exact overlap
            offset_lon = pt.x + 0.0005
            position_copy = from_shape(ShapelyPoint(offset_lon, pt.y), srid=4326)
        except Exception:
            pass

    # Create new unit
    new_unit = Unit(
        session_id=session_id,
        side=unit.side,
        name=name_b,
        unit_type=unit.unit_type,
        sidc=unit.sidc,
        parent_unit_id=unit.parent_unit_id,
        position=position_copy,
        heading_deg=unit.heading_deg,
        strength=unit.strength * ratio,
        ammo=unit.ammo,
        morale=unit.morale,
        suppression=unit.suppression,
        comms_status=unit.comms_status,
        capabilities=dict(unit.capabilities) if unit.capabilities else None,
        move_speed_mps=unit.move_speed_mps,
        detection_range_m=unit.detection_range_m,
        assigned_user_ids=list(unit.assigned_user_ids) if unit.assigned_user_ids else None,
    )
    db.add(new_unit)

    # Update original
    unit.name = name_a
    unit.strength = unit.strength * (1 - ratio)

    await db.flush()
    await db.refresh(new_unit)

    return {
        "original": _serialize_unit(unit),
        "new_unit": _serialize_unit(new_unit),
    }


@router.post("/{session_id}/units/{unit_id}/merge")
async def merge_units(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitMergeRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Merge another unit into this one. Both must be the same type and side.
    The surviving unit gains combined strength (capped at 1.0).
    Ammo and morale are weighted averages. The merged unit is destroyed.
    Requires command authority over both units."""
    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    survivor = result.scalar_one_or_none()
    if survivor is None:
        raise HTTPException(status_code=404, detail="Surviving unit not found")

    try:
        merge_uid = uuid.UUID(body.merge_with_unit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid merge_with_unit_id")

    result2 = await db.execute(
        select(Unit).where(Unit.id == merge_uid, Unit.session_id == session_id)
    )
    absorbed = result2.scalar_one_or_none()
    if absorbed is None:
        raise HTTPException(status_code=404, detail="Unit to merge not found")

    # Validation
    if survivor.unit_type != absorbed.unit_type:
        raise HTTPException(status_code=400, detail="Cannot merge units of different types")
    if survivor.side != absorbed.side:
        raise HTTPException(status_code=400, detail="Cannot merge units from different sides")
    if str(survivor.id) == str(absorbed.id):
        raise HTTPException(status_code=400, detail="Cannot merge a unit with itself")

    if side not in ("admin", "observer"):
        if survivor.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot merge units from other side")
        auth1 = await check_command_authority(user_id, survivor, session_id, db)
        if not auth1:
            auth1 = await check_subordinate_authority(user_id, survivor, session_id, db)
        auth2 = await check_command_authority(user_id, absorbed, session_id, db)
        if not auth2:
            auth2 = await check_subordinate_authority(user_id, absorbed, session_id, db)
        if not auth1 or not auth2:
            raise HTTPException(status_code=403, detail="Need command authority over both units")

    # Weighted combine
    total_str = survivor.strength + absorbed.strength
    if total_str > 0:
        w_surv = survivor.strength / total_str
        w_abs = absorbed.strength / total_str
    else:
        w_surv = 0.5
        w_abs = 0.5

    survivor.strength = min(1.0, total_str)
    survivor.ammo = min(1.0, survivor.ammo * w_surv + absorbed.ammo * w_abs)
    survivor.morale = min(1.0, survivor.morale * w_surv + absorbed.morale * w_abs)
    survivor.suppression = max(0.0, survivor.suppression * w_surv + absorbed.suppression * w_abs)

    # Re-parent any children of the absorbed unit to the survivor
    child_result = await db.execute(
        select(Unit).where(Unit.parent_unit_id == absorbed.id)
    )
    for child in child_result.scalars().all():
        child.parent_unit_id = survivor.id

    # Remove the absorbed unit
    absorbed.is_destroyed = True
    absorbed.strength = 0.0
    absorbed.current_task = None

    # Clean up name if it has /N suffixes from a previous split
    if "/" in survivor.name:
        prefix = survivor.name.rsplit("/", 1)[0]
        survivor.name = prefix

    await db.flush()

    return {
        "survivor": _serialize_unit(survivor),
        "absorbed_id": str(absorbed.id),
    }


@router.get("/{session_id}/contacts")
async def get_contacts(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return contacts visible to the requester's side."""
    side = participant.side.value
    if side not in ("blue", "red"):
        side = "blue"
    return await get_visible_contacts(session_id, side, db)
