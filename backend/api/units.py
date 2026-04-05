"""Units API – fog-of-war filtered unit and contact retrieval."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.services.visibility_service import get_visible_units, get_visible_contacts

router = APIRouter()


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


@router.get("/{session_id}/contacts")
async def get_contacts(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return contacts visible to the requester's side."""
    side = participant.side.value
    return await get_visible_contacts(session_id, side, db)
