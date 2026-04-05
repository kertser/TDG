"""Reports API – query reports (sitreps, spotreps, etc.)."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.models.report import Report

router = APIRouter()


@router.get("/{session_id}/reports")
async def list_reports(
    session_id: uuid.UUID,
    channel: str | None = Query(None),
    since_tick: int | None = Query(None),
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    query = select(Report).where(Report.session_id == session_id)
    if channel:
        query = query.where(Report.channel == channel)
    if since_tick is not None:
        query = query.where(Report.tick >= since_tick)
    query = query.order_by(Report.tick.asc(), Report.created_at.asc())

    result = await db.execute(query)
    reports = result.scalars().all()

    side = participant.side.value
    return [
        {
            "id": str(r.id),
            "tick": r.tick,
            "channel": r.channel,
            "text": r.text,
            "structured_data": r.structured_data,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        }
        for r in reports
        if r.to_side.value == side or side in ("admin", "observer")
    ]

