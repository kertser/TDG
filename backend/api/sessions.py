"""Session CRUD endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, delete as sa_delete
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.models.session import Session, SessionParticipant, SessionStatus, Side
from backend.models.scenario import Scenario

router = APIRouter()


# ── Schemas ───────────────────────────────────────────

class SessionCreate(BaseModel):
    scenario_id: str
    settings: dict | None = None


class SessionJoin(BaseModel):
    side: str = "blue"
    role: str = "commander"


class SessionRead(BaseModel):
    id: str
    scenario_id: str
    status: str
    tick: int
    tick_interval: int
    current_time: datetime | None = None
    participant_count: int = 0
    created_at: datetime


class ParticipantRead(BaseModel):
    id: str
    user_id: str
    display_name: str
    side: str
    role: str


# ── Endpoints ─────────────────────────────────────────

@router.post("", response_model=SessionRead)
async def create_session(body: SessionCreate, db: DB, user: CurrentUser):
    # Verify scenario exists
    result = await db.execute(select(Scenario).where(Scenario.id == uuid.UUID(body.scenario_id)))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    session = Session(
        scenario_id=scenario.id,
        status=SessionStatus.lobby,
        tick=0,
        tick_interval=60,
        current_time=datetime.now(timezone.utc),
        settings=body.settings,
    )
    db.add(session)
    await db.flush()

    # Creator auto-joins as admin
    participant = SessionParticipant(
        session_id=session.id,
        user_id=user.id,
        side=Side.admin,
        role="creator",
    )
    db.add(participant)
    await db.flush()

    return SessionRead(
        id=str(session.id),
        scenario_id=str(session.scenario_id),
        status=session.status.value,
        tick=session.tick,
        tick_interval=session.tick_interval,
        current_time=session.current_time,
        participant_count=1,
        created_at=session.created_at,
    )


@router.get("", response_model=list[SessionRead])
async def list_sessions(db: DB, user: CurrentUser):
    """List sessions the current user participates in."""
    result = await db.execute(
        select(Session)
        .join(SessionParticipant, SessionParticipant.session_id == Session.id)
        .where(SessionParticipant.user_id == user.id)
        .options(selectinload(Session.participants))
    )
    sessions = result.scalars().unique().all()
    return [
        SessionRead(
            id=str(s.id),
            scenario_id=str(s.scenario_id),
            status=s.status.value,
            tick=s.tick,
            tick_interval=s.tick_interval,
            current_time=s.current_time,
            participant_count=len(s.participants),
            created_at=s.created_at,
        )
        for s in sessions
    ]


@router.delete("", status_code=204)
async def delete_all_sessions(db: DB, user: CurrentUser):
    """Admin: delete all sessions and all cascade-dependent data."""
    from backend.models.event import Event
    from backend.models.report import Report
    from backend.models.contact import Contact
    from backend.models.order import Order, LocationReference
    from backend.models.overlay import PlanningOverlay
    from backend.models.red_agent import RedAgent
    from backend.models.unit import Unit
    from backend.models.grid import GridDefinition

    # Delete children first to avoid FK violations
    await db.execute(sa_delete(LocationReference))
    await db.execute(sa_delete(Event))
    await db.execute(sa_delete(Report))
    await db.execute(sa_delete(Contact))
    await db.execute(sa_delete(Order))
    await db.execute(sa_delete(PlanningOverlay))
    await db.execute(sa_delete(RedAgent))
    await db.execute(sa_delete(Unit))
    await db.execute(sa_delete(GridDefinition))
    await db.execute(sa_delete(SessionParticipant))
    await db.execute(sa_delete(Session))
    await db.flush()


@router.get("/{session_id}", response_model=SessionRead)
async def get_session(session_id: uuid.UUID, db: DB):
    result = await db.execute(
        select(Session).options(selectinload(Session.participants)).where(Session.id == session_id)
    )
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return SessionRead(
        id=str(s.id),
        scenario_id=str(s.scenario_id),
        status=s.status.value,
        tick=s.tick,
        tick_interval=s.tick_interval,
        current_time=s.current_time,
        participant_count=len(s.participants),
        created_at=s.created_at,
    )


@router.post("/{session_id}/join", response_model=ParticipantRead)
async def join_session(session_id: uuid.UUID, body: SessionJoin, db: DB, user: CurrentUser):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Check not already joined
    existing = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == user.id,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Already joined this session")

    try:
        side = Side(body.side)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid side: {body.side}")

    participant = SessionParticipant(
        session_id=session_id,
        user_id=user.id,
        side=side,
        role=body.role,
    )
    db.add(participant)
    await db.flush()

    return ParticipantRead(
        id=str(participant.id),
        user_id=str(user.id),
        display_name=user.display_name,
        side=participant.side.value,
        role=participant.role,
    )


@router.post("/{session_id}/start")
async def start_session(session_id: uuid.UUID, db: DB, user: CurrentUser):
    result = await db.execute(
        select(Session).options(selectinload(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.status != SessionStatus.lobby and session.status != SessionStatus.paused:
        raise HTTPException(status_code=400, detail=f"Cannot start session in state {session.status.value}")

    # Initialize units and grid from scenario (idempotent)
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, session.scenario, db)

    session.status = SessionStatus.running
    if session.current_time is None:
        session.current_time = datetime.now(timezone.utc)
    await db.flush()
    return {
        "status": session.status.value,
        "tick": session.tick,
        "current_time": session.current_time.isoformat() if session.current_time else None,
    }


@router.post("/{session_id}/pause")
async def pause_session(session_id: uuid.UUID, db: DB, user: CurrentUser):
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    session.status = SessionStatus.paused
    await db.flush()
    return {"status": session.status.value, "tick": session.tick}


@router.post("/{session_id}/tick")
async def advance_tick(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Advance one simulation tick — runs the full rules engine."""
    from backend.engine.tick import run_tick

    try:
        result = await run_tick(session_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Broadcast state update via WebSocket
    from backend.services.ws_manager import ws_manager
    from backend.services.visibility_service import get_visible_units, get_visible_contacts

    # Get visible state for each side and broadcast
    for side in ("blue", "red"):
        try:
            units = await get_visible_units(session_id, side, db)
            contacts = await get_visible_contacts(session_id, side, db)
            await ws_manager.broadcast(
                session_id,
                {
                    "type": "state_update",
                    "data": {
                        "units": units,
                        "contacts": contacts,
                        "tick": result["tick"],
                        "game_time": result.get("game_time"),
                    },
                },
                only_side=side,
            )
        except Exception:
            pass  # Don't fail tick on broadcast error

    # Also broadcast tick_update to all
    await ws_manager.broadcast(
        session_id,
        {"type": "tick_update", "data": {"tick": result["tick"], "game_time": result.get("game_time")}},
    )

    return result

