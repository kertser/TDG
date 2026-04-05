"""Events API – query game event log."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.event import Event

router = APIRouter()


@router.get("/{session_id}/events")
async def list_events(
    session_id: uuid.UUID,
    since_tick: int | None = Query(None),
    event_type: str | None = Query(None, alias="type"),
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    query = select(Event).where(Event.session_id == session_id)
    if since_tick is not None:
        query = query.where(Event.tick >= since_tick)
    if event_type:
        query = query.where(Event.event_type == event_type)
    query = query.order_by(Event.tick.asc(), Event.created_at.asc())

    result = await db.execute(query)
    events = result.scalars().all()

    # Filter by visibility
    side = participant.side.value
    visible = []
    for e in events:
        vis = e.visibility.value
        if vis == "all" or vis == side or side in ("admin", "observer"):
            visible.append({
                "id": str(e.id),
                "tick": e.tick,
                "event_type": e.event_type,
                "text_summary": e.text_summary,
                "payload": e.payload,
                "visibility": vis,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            })
    return visible

