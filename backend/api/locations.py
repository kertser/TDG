"""Location resolution API – resolve text to location references."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant

router = APIRouter()


class ResolveRequest(BaseModel):
    text: str


@router.post("/{session_id}/locations/resolve")
async def resolve_locations(
    session_id: uuid.UUID,
    body: ResolveRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Resolve location references from text. Stub – Phase 6 will implement full resolver."""
    return {"references": [], "raw_text": body.text, "note": "Stub – location resolver not yet implemented"}

