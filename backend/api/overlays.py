"""Planning Overlays API – CRUD for collaborative drawing layer."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.services import overlay_service

router = APIRouter()


class OverlayCreate(BaseModel):
    overlay_type: str
    geometry: dict
    style_json: dict | None = None
    label: str | None = None
    properties: dict | None = None


class OverlayUpdate(BaseModel):
    geometry: dict | None = None
    style_json: dict | None = None
    label: str | None = None
    properties: dict | None = None


@router.post("/{session_id}/overlays")
async def create_overlay(
    session_id: uuid.UUID,
    body: OverlayCreate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    result = await overlay_service.create_overlay(
        session_id=session_id,
        user_id=participant.user_id,
        side=participant.side.value,
        overlay_type=body.overlay_type,
        geometry=body.geometry,
        style_json=body.style_json,
        label=body.label,
        properties=body.properties,
        db=db,
    )
    return result


@router.get("/{session_id}/overlays")
async def list_overlays(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    return await overlay_service.list_overlays(
        session_id=session_id,
        side=participant.side.value,
        db=db,
    )


@router.put("/{session_id}/overlays/{overlay_id}")
async def update_overlay(
    session_id: uuid.UUID,
    overlay_id: uuid.UUID,
    body: OverlayUpdate,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    result = await overlay_service.update_overlay(
        session_id=session_id,
        overlay_id=overlay_id,
        geometry=body.geometry,
        style_json=body.style_json,
        label=body.label,
        properties=body.properties,
        db=db,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Overlay not found")
    return result


@router.delete("/{session_id}/overlays/{overlay_id}", status_code=204)
async def delete_overlay(
    session_id: uuid.UUID,
    overlay_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    success = await overlay_service.delete_overlay(
        session_id=session_id,
        overlay_id=overlay_id,
        db=db,
    )
    if not success:
        raise HTTPException(status_code=404, detail="Overlay not found")

