"""Units API – fog-of-war filtered unit and contact retrieval, unit assignment."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.services.visibility_service import get_visible_units, get_visible_contacts
from backend.models.unit import Unit

router = APIRouter()


class UnitAssignRequest(BaseModel):
    assigned_user_ids: list[str]


@router.get("/{session_id}/units")
async def get_units(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return units visible to the requester's side (fog-of-war filtered)."""
    side = participant.side.value
    return await get_visible_units(session_id, side, db)


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

        # If the unit already has owners, only an existing owner can modify
        if len(current_owners) > 0 and user_id not in current_owners:
            raise HTTPException(
                status_code=403,
                detail="Only the unit owner, admin, or referee can assign this unit"
            )
        # If the unit has no owners, same-side user can claim it (first assignment)

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
    return await get_visible_contacts(session_id, side, db)
