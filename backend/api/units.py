"""Units API – fog-of-war filtered unit and contact retrieval, unit assignment, hierarchy."""

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
    _serialize_unit,
)
from backend.models.unit import Unit

router = APIRouter()


class UnitAssignRequest(BaseModel):
    assigned_user_ids: list[str]


class UnitRenameRequest(BaseModel):
    name: str


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
            # Fall back to original rules: owner or unassigned
            if len(current_owners) > 0 and user_id not in current_owners:
                raise HTTPException(
                    status_code=403,
                    detail="Only commanders, the unit owner, admin, or referee can assign this unit",
                )

    unit.assigned_user_ids = body.assigned_user_ids if body.assigned_user_ids else None
    await db.flush()
    return {"id": str(unit.id), "assigned_user_ids": unit.assigned_user_ids}


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
