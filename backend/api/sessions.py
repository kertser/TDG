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
    name: str | None = None
    scenario_title: str | None = None
    scenario_description: str | None = None
    scenario_environment: dict | None = None
    scenario_objectives: dict | None = None


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
        name=scenario.title,  # default name from scenario
        status=SessionStatus.lobby,
        tick=0,
        tick_interval=60,
        current_time=datetime.now(timezone.utc),
        settings=body.settings,
    )
    db.add(session)
    await db.flush()

    # Creator auto-joins as blue player
    participant = SessionParticipant(
        session_id=session.id,
        user_id=user.id,
        side=Side.blue,
        role="commander",
    )
    db.add(participant)
    await db.flush()

    # Auto-initialize grid and units from scenario (available in lobby)
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, scenario, db)

    return SessionRead(
        id=str(session.id),
        scenario_id=str(session.scenario_id),
        status=session.status.value,
        tick=session.tick,
        tick_interval=session.tick_interval,
        current_time=session.current_time,
        participant_count=1,
        created_at=session.created_at,
        name=session.name or scenario.title,
    )


@router.get("", response_model=list[SessionRead])
async def list_sessions(db: DB, user: CurrentUser):
    """List sessions the current user participates in."""
    result = await db.execute(
        select(Session)
        .join(SessionParticipant, SessionParticipant.session_id == Session.id)
        .where(SessionParticipant.user_id == user.id)
        .options(selectinload(Session.participants), selectinload(Session.scenario))
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
            name=s.name or (s.scenario.title if s.scenario else None),
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
        select(Session).options(selectinload(Session.participants), selectinload(Session.scenario)).where(Session.id == session_id)
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
        name=s.name or (s.scenario.title if s.scenario else None),
        scenario_title=s.scenario.title if s.scenario else None,
        scenario_description=s.scenario.description if s.scenario else None,
        scenario_environment=s.scenario.environment if s.scenario else None,
        scenario_objectives=s.scenario.objectives if s.scenario else None,
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

    # Enforce: observer side can only have observer role
    role = body.role
    if side == Side.observer and role in ("commander", "officer"):
        role = "observer"

    participant = SessionParticipant(
        session_id=session_id,
        user_id=user.id,
        side=side,
        role=role,
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


@router.get("/{session_id}/participants")
async def list_session_participants(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """List all participants in a session. Requires the caller to be a participant."""
    # Verify user is a participant
    me = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == user.id,
        )
    )
    if me.scalar_one_or_none() is None:
        raise HTTPException(status_code=403, detail="Not a participant in this session")

    result = await db.execute(
        select(SessionParticipant)
        .options(selectinload(SessionParticipant.user))
        .where(SessionParticipant.session_id == session_id)
    )
    participants = result.scalars().all()
    return [
        {
            "id": str(p.id),
            "user_id": str(p.user_id),
            "display_name": p.user.display_name if p.user else "?",
            "side": p.side.value,
            "role": p.role,
        }
        for p in participants
    ]


@router.get("/{session_id}/my-role")
async def get_my_role(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Return the current user's side and role in this session."""
    result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == user.id,
        )
    )
    participant = result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=404, detail="Not a participant")
    return {
        "side": participant.side.value,
        "role": participant.role,
        "can_advance_turn": participant.role in ("commander", "admin") or participant.side == Side.admin,
    }


@router.post("/{session_id}/start")
async def start_session(session_id: uuid.UUID, db: DB, user: CurrentUser):
    result = await db.execute(
        select(Session).options(selectinload(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Already running — return current state (idempotent)
    if session.status == SessionStatus.running:
        return {
            "status": session.status.value,
            "tick": session.tick,
            "current_time": session.current_time.isoformat() if session.current_time else None,
        }

    if session.status == SessionStatus.finished:
        raise HTTPException(status_code=400, detail="Cannot start a finished session — reset it first")

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
    """Advance one simulation tick — runs the full rules engine.
    Only commanders and admins can advance turns."""
    from backend.engine.tick import run_tick

    # Check that user is a commander or admin
    part_result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == user.id,
        )
    )
    participant = part_result.scalar_one_or_none()
    if participant is None:
        raise HTTPException(status_code=403, detail="Not a participant in this session")
    if participant.role not in ("commander", "admin") and participant.side not in (Side.admin,):
        raise HTTPException(
            status_code=403,
            detail="Only commanders or admins can advance turns",
        )

    try:
        result = await run_tick(session_id, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Commit tick changes BEFORE broadcasting / returning response.
    # This ensures subsequent queries (e.g. pending-orders-count) see
    # the updated order statuses immediately.
    await db.commit()

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

    # Broadcast newly discovered map objects to the relevant side
    # Check events table for object_discovered events from this tick
    from backend.models.event import Event
    from backend.models.map_object import MapObject
    from backend.api.map_objects import _serialize_map_object
    try:
        disc_result = await db.execute(
            select(Event).where(
                Event.session_id == session_id,
                Event.tick == result["tick"] - 1,  # events created at old tick
                Event.event_type == "object_discovered",
            )
        )
        disc_events = disc_result.scalars().all()
        if disc_events:
            # Collect unique object IDs and their discovering side
            discovered_ids = {}
            for evt in disc_events:
                payload = evt.payload or {}
                oid = payload.get("object_id")
                side = payload.get("side")
                if oid and side:
                    discovered_ids.setdefault(oid, set()).add(side)

            if discovered_ids:
                from sqlalchemy.dialects.postgresql import UUID as PG_UUID
                mo_result = await db.execute(
                    select(MapObject).where(
                        MapObject.session_id == session_id,
                        MapObject.id.in_([uuid.UUID(oid) for oid in discovered_ids]),
                    )
                )
                disc_objects = mo_result.scalars().all()
                for obj in disc_objects:
                    serialized = _serialize_map_object(obj)
                    oid_str = str(obj.id)
                    sides = discovered_ids.get(oid_str, set())
                    for side in sides:
                        await ws_manager.broadcast(
                            session_id,
                            {"type": "map_object_updated", "data": serialized},
                            only_side=side,
                        )
    except Exception:
        pass

    # Broadcast smoke object updates (decay / dissipation) to all clients
    try:
        smoke_updated = result.get("_smoke_updated", [])
        for smoke_obj in smoke_updated:
            serialized = _serialize_map_object(smoke_obj)
            if smoke_obj.is_active:
                await ws_manager.broadcast(
                    session_id,
                    {"type": "map_object_updated", "data": serialized},
                )
            else:
                # Smoke dissipated — send deletion so frontend removes it
                await ws_manager.broadcast(
                    session_id,
                    {"type": "map_object_deleted", "data": {"id": str(smoke_obj.id), "object_id": str(smoke_obj.id)}},
                )
    except Exception:
        pass

    # Also broadcast tick_update to all
    # Include combat impact locations for visual effects
    combat_impacts = []
    for evt_dict in result.get("_raw_events", []):
        etype = evt_dict.get("event_type", "")
        if etype in ("combat", "unit_destroyed", "artillery_support"):
            payload = evt_dict.get("payload", {})
            if payload.get("target_lat") and payload.get("target_lon"):
                combat_impacts.append({
                    "type": etype,
                    "lat": payload["target_lat"],
                    "lon": payload["target_lon"],
                    "is_artillery": payload.get("is_artillery", etype == "artillery_support"),
                })

    # Collect game_finished events for broadcast
    game_events = []
    for evt_dict in result.get("_raw_events", []):
        etype = evt_dict.get("event_type", "")
        if etype == "game_finished":
            game_events.append({
                "event_type": etype,
                "text_summary": evt_dict.get("text_summary", ""),
                "payload": evt_dict.get("payload", {}),
            })

    await ws_manager.broadcast(
        session_id,
        {"type": "tick_update", "data": {
            "tick": result["tick"],
            "game_time": result.get("game_time"),
            "combat_impacts": combat_impacts,
            "events": game_events,
        }},
    )

    # Broadcast radio chatter messages generated during tick
    radio_messages = result.get("radio_messages", [])
    for msg in radio_messages:
        msg_side = msg.get("side", "blue")
        await ws_manager.broadcast(
            session_id,
            {"type": "chat_message", "data": msg},
            only_side=msg_side,
        )

    # Broadcast reports (SPOTREPs, SHELREPs, SITREPs, INTSUMs, CASREPs)
    tick_reports = result.get("reports", [])
    for rpt in tick_reports:
        rpt_side = rpt.get("to_side", "blue")
        await ws_manager.broadcast(
            session_id,
            {"type": "report_new", "data": rpt},
            only_side=rpt_side,
        )

    # Strip internal data before returning
    result.pop("_raw_events", None)

    return result

