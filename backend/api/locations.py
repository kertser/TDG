"""Location resolution API – resolve text to location references."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
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
    """Resolve location references from text using GridService."""
    import re
    from backend.models.grid import GridDefinition
    from backend.services.location_resolver import LocationResolver
    from backend.schemas.order import LocationRefRaw

    # Load grid
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = result.scalar_one_or_none()
    grid_service = None
    if gd:
        from backend.services.grid_service import GridService
        grid_service = GridService(gd)

    resolver = LocationResolver(grid_service=grid_service)

    # Extract potential references from raw text using regex patterns
    text = body.text
    raw_refs: list[LocationRefRaw] = []

    # Snail paths: B8-2-4, C7-8-3
    for m in re.finditer(r'[A-Za-z]\d+(?:-\d){1,3}', text):
        raw_refs.append(LocationRefRaw(
            source_text=m.group(), ref_type="snail", normalized=m.group().upper()
        ))

    # Grid squares: B8, C7 (only if not already part of a snail path)
    for m in re.finditer(r'\b([A-Za-z])(\d{1,2})\b', text):
        full = m.group().upper()
        if not any(r.normalized.startswith(full + "-") for r in raw_refs):
            raw_refs.append(LocationRefRaw(
                source_text=m.group(), ref_type="grid", normalized=full
            ))

    # Coordinates: 48.85,2.35
    for m in re.finditer(r'(-?\d+\.?\d*)\s*[,;]\s*(-?\d+\.?\d*)', text):
        raw_refs.append(LocationRefRaw(
            source_text=m.group(), ref_type="coordinate", normalized=m.group()
        ))

    # Resolve all
    resolved = resolver.resolve_all(raw_refs)

    return {
        "references": [
            loc.model_dump(mode="json", exclude_none=True)
            for loc in resolved
        ],
        "raw_text": body.text,
    }

