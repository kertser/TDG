"""Admin API endpoints – god-view, unit CRUD, participants, event injection, DB stats, user management."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select, func, delete as sa_delete
from sqlalchemy.orm import selectinload

from backend.api.deps import DB, CurrentUser
from backend.config import settings
from backend.models.session import Session, SessionParticipant, SessionStatus, Side
from backend.models.scenario import Scenario
from backend.models.unit import Unit
from backend.models.order import Order, LocationReference
from backend.models.overlay import PlanningOverlay
from backend.models.contact import Contact
from backend.models.event import Event
from backend.models.report import Report
from backend.models.red_agent import RedAgent
from backend.models.grid import GridDefinition
from backend.models.user import User
from backend.services.visibility_service import get_visible_units

router = APIRouter()


# ── Admin Password Verification ─────────────────────

class AdminPasswordCheck(BaseModel):
    password: str


@router.post("/verify-password")
async def verify_admin_password(body: AdminPasswordCheck):
    """Verify admin password – returns {ok: true} if correct."""
    if body.password == settings.ADMIN_PASSWORD:
        return {"ok": True}
    raise HTTPException(status_code=403, detail="Invalid admin password")


# ══════════════════════════════════════════════════════
# ── User Management ──────────────────────────────────
# ══════════════════════════════════════════════════════

@router.get("/users")
async def admin_list_users(db: DB):
    """List all registered users."""
    result = await db.execute(select(User).order_by(User.created_at.desc()))
    users = result.scalars().all()
    return [
        {
            "id": str(u.id),
            "display_name": u.display_name,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in users
    ]


class AdminUserCreate(BaseModel):
    display_name: str


@router.post("/users")
async def admin_create_user(body: AdminUserCreate, db: DB):
    """Admin: create a new user."""
    if not body.display_name.strip():
        raise HTTPException(status_code=400, detail="display_name required")
    user = User(display_name=body.display_name.strip())
    db.add(user)
    await db.flush()
    return {"id": str(user.id), "display_name": user.display_name}


class AdminUserUpdate(BaseModel):
    display_name: str | None = None


@router.put("/users/{user_id}")
async def admin_update_user(user_id: uuid.UUID, body: AdminUserUpdate, db: DB):
    """Admin: rename a user."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if body.display_name is not None:
        user.display_name = body.display_name.strip()
    await db.flush()
    return {"id": str(user.id), "display_name": user.display_name}


@router.delete("/users/{user_id}", status_code=204)
async def admin_delete_user(user_id: uuid.UUID, db: DB):
    """Admin: delete a user and all their session participations."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Remove participations
    await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.user_id == user_id))
    await db.delete(user)
    await db.flush()


class AdminBulkDelete(BaseModel):
    user_ids: list[str]


@router.post("/users/bulk-delete", status_code=200)
async def admin_bulk_delete_users(body: AdminBulkDelete, db: DB):
    """Admin: bulk delete multiple users and their session participations."""
    deleted = 0
    for uid_str in body.user_ids:
        try:
            uid = uuid.UUID(uid_str)
        except ValueError:
            continue
        result = await db.execute(select(User).where(User.id == uid))
        user = result.scalar_one_or_none()
        if user is None:
            continue
        await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.user_id == uid))
        await db.delete(user)
        deleted += 1
    await db.flush()
    return {"deleted": deleted, "requested": len(body.user_ids)}


# ══════════════════════════════════════════════════════
# ── Unit Hierarchy (chain of command) ────────────────
# ══════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/unit-hierarchy")
async def admin_get_unit_hierarchy(session_id: uuid.UUID, db: DB):
    """Return all units for a session organized as a hierarchy tree,
    with assigned user display names and commanding user info."""
    result = await db.execute(
        select(Unit).where(Unit.session_id == session_id, Unit.is_destroyed == False)
    )
    units = result.scalars().all()

    # Fetch all participants for this session to resolve user names
    part_result = await db.execute(
        select(SessionParticipant)
        .options(selectinload(SessionParticipant.user))
        .where(SessionParticipant.session_id == session_id)
    )
    participants = part_result.scalars().all()
    user_map = {}  # user_id (str) -> display_name
    for p in participants:
        if p.user:
            user_map[str(p.user_id)] = p.user.display_name

    from backend.services.visibility_service import _serialize_unit
    serialized = []
    for u in units:
        data = _serialize_unit(u)
        # Add assigned user names
        names = []
        if u.assigned_user_ids:
            for uid in u.assigned_user_ids:
                name = user_map.get(uid)
                if name:
                    names.append(name)
        data["assigned_user_names"] = names
        serialized.append(data)

    # Build lookup for resolving commanding user
    unit_map = {str(u.id): u for u in units}

    # For each unit, walk up the parent chain to find the nearest unit with an assigned user
    for data in serialized:
        cmd_name = None
        current_id = data["id"]
        visited = set()
        while current_id and current_id not in visited:
            visited.add(current_id)
            u = unit_map.get(current_id)
            if u is None:
                break
            if u.assigned_user_ids:
                for uid in u.assigned_user_ids:
                    name = user_map.get(uid)
                    if name:
                        cmd_name = name
                        break
                if cmd_name:
                    break
            current_id = str(u.parent_unit_id) if u.parent_unit_id else None
        data["commanding_user_name"] = cmd_name

    return serialized


class UnitParentUpdate(BaseModel):
    parent_unit_id: str | None = None


# ── Admin Add Participant to Session ─────────────

class AdminAddParticipant(BaseModel):
    user_id: str
    side: str = "blue"
    role: str = "commander"


@router.api_route("/sessions/{session_id}/add-participant", methods=["POST", "PUT"])
async def admin_add_participant(
    session_id: uuid.UUID, body: AdminAddParticipant, db: DB,
):
    """Admin: add a user to a session as a participant with given side/role."""
    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Verify user exists
    try:
        uid = uuid.UUID(body.user_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid user_id")

    result = await db.execute(select(User).where(User.id == uid))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")

    # Check if already a participant
    existing = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == uid,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"User '{user.display_name}' already in this session")

    # Validate side
    try:
        side = Side(body.side)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid side: {body.side}")

    participant = SessionParticipant(
        session_id=session_id,
        user_id=uid,
        side=side,
        role=body.role or "commander",
    )
    db.add(participant)
    await db.flush()

    return {
        "id": str(participant.id),
        "user_id": str(uid),
        "display_name": user.display_name,
        "side": participant.side.value,
        "role": participant.role,
    }


@router.put("/sessions/{session_id}/units/{unit_id}/parent")
async def admin_set_unit_parent(
    session_id: uuid.UUID, unit_id: uuid.UUID,
    body: UnitParentUpdate, db: DB,
):
    """Admin: set or clear a unit's parent (chain of command)."""
    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if body.parent_unit_id:
        parent_uuid = uuid.UUID(body.parent_unit_id)
        # Verify parent exists in same session
        pr = await db.execute(select(Unit).where(Unit.id == parent_uuid, Unit.session_id == session_id))
        if pr.scalar_one_or_none() is None:
            raise HTTPException(status_code=404, detail="Parent unit not found in session")
        # Prevent self-reference
        if parent_uuid == unit_id:
            raise HTTPException(status_code=400, detail="Unit cannot be its own parent")
        unit.parent_unit_id = parent_uuid
    else:
        unit.parent_unit_id = None

    await db.flush()
    from backend.services.visibility_service import _serialize_unit
    return _serialize_unit(unit)


# ── Schemas ─────────────────────────────────────────

class AdminUnitCreate(BaseModel):
    side: str = "blue"
    name: str
    unit_type: str
    sidc: str = ""
    lat: float | None = None
    lon: float | None = None
    heading_deg: float = 0.0
    strength: float = 1.0
    ammo: float = 1.0
    morale: float = 0.9
    move_speed_mps: float = 4.0
    detection_range_m: float = 1500.0
    capabilities: dict | None = None
    parent_unit_id: str | None = None
    assigned_user_ids: list[str] | None = None


class AdminUnitUpdate(BaseModel):
    name: str | None = None
    side: str | None = None
    unit_type: str | None = None
    sidc: str | None = None
    lat: float | None = None
    lon: float | None = None
    heading_deg: float | None = None
    strength: float | None = None
    ammo: float | None = None
    morale: float | None = None
    suppression: float | None = None
    move_speed_mps: float | None = None
    detection_range_m: float | None = None
    capabilities: dict | None = None
    current_task: dict | None = None
    parent_unit_id: str | None = None
    assigned_user_ids: list[str] | None = None
    is_destroyed: bool | None = None


class ParticipantUpdate(BaseModel):
    side: str | None = None
    role: str | None = None


class EventInject(BaseModel):
    event_type: str = "custom"
    visibility: str = "all"
    text_summary: str = ""
    payload: dict | None = None


class TickIntervalUpdate(BaseModel):
    tick_interval: int


# ── DB Stats ────────────────────────────────────────

@router.get("/sessions")
async def admin_list_all_sessions(db: DB):
    """Admin: list ALL sessions regardless of participation."""
    result = await db.execute(
        select(Session).order_by(Session.created_at.desc())
    )
    sessions = result.scalars().all()
    out = []
    for s in sessions:
        # Count participants
        cnt_result = await db.execute(
            select(func.count()).select_from(SessionParticipant).where(
                SessionParticipant.session_id == s.id
            )
        )
        cnt = cnt_result.scalar() or 0
        out.append({
            "id": str(s.id),
            "scenario_id": str(s.scenario_id) if s.scenario_id else None,
            "status": s.status.value,
            "tick": s.tick,
            "participant_count": cnt,
            "created_at": s.created_at.isoformat() if s.created_at else None,
        })
    return out


@router.get("/stats")
async def db_stats(db: DB, user: CurrentUser):
    """Return row counts for all major tables."""
    tables = {
        "sessions": Session,
        "participants": SessionParticipant,
        "scenarios": Scenario,
        "units": Unit,
        "orders": Order,
        "location_references": LocationReference,
        "overlays": PlanningOverlay,
        "contacts": Contact,
        "events": Event,
        "reports": Report,
        "red_agents": RedAgent,
        "grid_definitions": GridDefinition,
    }
    stats = {}
    for name, model in tables.items():
        try:
            result = await db.execute(select(func.count()).select_from(model))
            stats[name] = result.scalar() or 0
        except Exception:
            stats[name] = -1
    return stats


# ── Scenario Deletion ───────────────────────────────

@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: uuid.UUID, db: DB, user: CurrentUser):
    """Delete a scenario and all sessions associated with it."""
    result = await db.execute(
        select(Session.id).where(Session.scenario_id == scenario_id)
    )
    session_ids = [row[0] for row in result.all()]

    if session_ids:
        await db.execute(sa_delete(LocationReference).where(LocationReference.session_id.in_(session_ids)))
        await db.execute(sa_delete(Event).where(Event.session_id.in_(session_ids)))
        await db.execute(sa_delete(Report).where(Report.session_id.in_(session_ids)))
        await db.execute(sa_delete(Contact).where(Contact.session_id.in_(session_ids)))
        await db.execute(sa_delete(Order).where(Order.session_id.in_(session_ids)))
        await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id.in_(session_ids)))
        await db.execute(sa_delete(RedAgent).where(RedAgent.session_id.in_(session_ids)))
        await db.execute(sa_delete(Unit).where(Unit.session_id.in_(session_ids)))
        await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id.in_(session_ids)))
        await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.session_id.in_(session_ids)))
        await db.execute(sa_delete(Session).where(Session.scenario_id == scenario_id))

    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await db.delete(scenario)
    await db.flush()


# ══════════════════════════════════════════════════════
# ── God-View Units (all units, no fog-of-war) ────────
# ══════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/units")
async def admin_get_all_units(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Return ALL units for a session (god view — no fog-of-war filtering)."""
    return await get_visible_units(session_id, "admin", db)


@router.post("/sessions/{session_id}/units")
async def admin_create_unit(session_id: uuid.UUID, body: AdminUnitCreate, db: DB, user: CurrentUser):
    """Create a new unit mid-session (reinforcements injection)."""
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point as ShapelyPoint

    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    if result.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Session not found")

    position = None
    if body.lat is not None and body.lon is not None:
        position = from_shape(ShapelyPoint(body.lon, body.lat), srid=4326)

    unit = Unit(
        session_id=session_id,
        side=body.side,
        name=body.name,
        unit_type=body.unit_type,
        sidc=body.sidc,
        position=position,
        heading_deg=body.heading_deg,
        strength=body.strength,
        ammo=body.ammo,
        morale=body.morale,
        move_speed_mps=body.move_speed_mps,
        detection_range_m=body.detection_range_m,
        capabilities=body.capabilities,
        parent_unit_id=uuid.UUID(body.parent_unit_id) if body.parent_unit_id else None,
        assigned_user_ids=body.assigned_user_ids,
    )
    db.add(unit)
    await db.flush()
    await db.refresh(unit)

    from backend.services.visibility_service import _serialize_unit
    return _serialize_unit(unit)


@router.put("/sessions/{session_id}/units/{unit_id}")
async def admin_update_unit(
    session_id: uuid.UUID, unit_id: uuid.UUID,
    body: AdminUnitUpdate, db: DB, user: CurrentUser,
):
    """Admin: update any field on a unit."""
    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if body.name is not None:
        unit.name = body.name
    if body.side is not None:
        unit.side = body.side
    if body.unit_type is not None:
        unit.unit_type = body.unit_type
    if body.sidc is not None:
        unit.sidc = body.sidc
    if body.lat is not None and body.lon is not None:
        from geoalchemy2.shape import from_shape
        from shapely.geometry import Point as ShapelyPoint
        unit.position = from_shape(ShapelyPoint(body.lon, body.lat), srid=4326)
    if body.heading_deg is not None:
        unit.heading_deg = body.heading_deg
    if body.strength is not None:
        unit.strength = body.strength
    if body.ammo is not None:
        unit.ammo = body.ammo
    if body.morale is not None:
        unit.morale = body.morale
    if body.suppression is not None:
        unit.suppression = body.suppression
    if body.move_speed_mps is not None:
        unit.move_speed_mps = body.move_speed_mps
    if body.detection_range_m is not None:
        unit.detection_range_m = body.detection_range_m
    if body.capabilities is not None:
        unit.capabilities = body.capabilities
    if body.current_task is not None:
        unit.current_task = body.current_task
    if body.parent_unit_id is not None:
        unit.parent_unit_id = uuid.UUID(body.parent_unit_id) if body.parent_unit_id != "" else None
    if body.assigned_user_ids is not None:
        unit.assigned_user_ids = body.assigned_user_ids if body.assigned_user_ids else None
    if body.is_destroyed is not None:
        unit.is_destroyed = body.is_destroyed

    await db.flush()
    await db.refresh(unit)

    from backend.services.visibility_service import _serialize_unit
    return _serialize_unit(unit)


@router.delete("/sessions/{session_id}/units/{unit_id}", status_code=204)
async def admin_delete_unit(session_id: uuid.UUID, unit_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: delete a unit from a session."""
    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    await db.delete(unit)
    await db.flush()


# ══════════════════════════════════════════════════════
# ── Participants Management ──────────────────────────
# ══════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/participants")
async def admin_list_participants(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """List all participants with user display names."""
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
            "joined_at": p.joined_at.isoformat() if p.joined_at else None,
        }
        for p in participants
    ]


@router.put("/sessions/{session_id}/participants/{participant_id}")
async def admin_update_participant(
    session_id: uuid.UUID, participant_id: uuid.UUID,
    body: ParticipantUpdate, db: DB, user: CurrentUser,
):
    """Admin: update a participant's side or role."""
    result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.id == participant_id,
            SessionParticipant.session_id == session_id,
        )
    )
    p = result.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Participant not found")

    if body.side is not None:
        try:
            p.side = Side(body.side)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid side: {body.side}")
    if body.role is not None:
        p.role = body.role

    await db.flush()
    return {"id": str(p.id), "side": p.side.value, "role": p.role}


@router.delete("/sessions/{session_id}/participants/{participant_id}", status_code=204)
async def admin_kick_participant(
    session_id: uuid.UUID, participant_id: uuid.UUID, db: DB, user: CurrentUser,
):
    """Admin: remove a participant from a session."""
    result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.id == participant_id,
            SessionParticipant.session_id == session_id,
        )
    )
    p = result.scalar_one_or_none()
    if p is None:
        raise HTTPException(status_code=404, detail="Participant not found")
    await db.delete(p)
    await db.flush()


# ══════════════════════════════════════════════════════
# ── Session Controls ─────────────────────────────────
# ══════════════════════════════════════════════════════

@router.put("/sessions/{session_id}/tick-interval")
async def admin_set_tick_interval(
    session_id: uuid.UUID, body: TickIntervalUpdate, db: DB, user: CurrentUser,
):
    """Admin: change the tick interval (seconds) for a session. Frontend sends minutes, backend stores seconds."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.tick_interval < 1:
        raise HTTPException(status_code=400, detail="Tick interval must be >= 1")
    session.tick_interval = body.tick_interval
    await db.flush()
    return {"tick_interval": session.tick_interval}


@router.post("/sessions/{session_id}/reset")
async def admin_reset_session(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: reset session to tick 0, re-create units from scenario."""
    result = await db.execute(
        select(Session).options(selectinload(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete all game-state data
    await db.execute(sa_delete(LocationReference).where(LocationReference.session_id == session_id))
    await db.execute(sa_delete(Event).where(Event.session_id == session_id))
    await db.execute(sa_delete(Report).where(Report.session_id == session_id))
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(Order).where(Order.session_id == session_id))
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))

    # Reset session state
    session.tick = 0
    session.status = SessionStatus.lobby
    session.current_time = datetime.now(timezone.utc)
    await db.flush()

    # Re-initialize from scenario
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, session.scenario, db)

    return {"status": session.status.value, "tick": 0, "message": "Session reset to turn 0"}


# ══════════════════════════════════════════════════════
# ── Event Injection ──────────────────────────────────
# ══════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/events")
async def admin_inject_event(
    session_id: uuid.UUID, body: EventInject, db: DB, user: CurrentUser,
):
    """Admin: inject a custom event into the session."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    event = Event(
        session_id=session_id,
        tick=session.tick,
        game_timestamp=session.current_time or datetime.now(timezone.utc),
        event_type=body.event_type,
        visibility=body.visibility,
        payload=body.payload,
        text_summary=body.text_summary,
    )
    db.add(event)
    await db.flush()

    # Broadcast to all clients
    from backend.services.ws_manager import ws_manager
    await ws_manager.broadcast(
        session_id,
        {
            "type": "event_new",
            "data": {
                "id": str(event.id),
                "event_type": event.event_type,
                "text_summary": event.text_summary,
                "tick": event.tick,
                "visibility": event.visibility,
                "payload": event.payload,
            },
        },
    )

    return {"id": str(event.id), "event_type": event.event_type, "text_summary": event.text_summary}


# ══════════════════════════════════════════════════════
# ── All Orders (god view) ────────────────────────────
# ══════════════════════════════════════════════════════

@router.get("/sessions/{session_id}/orders")
async def admin_get_all_orders(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Return all orders from all sides."""
    result = await db.execute(
        select(Order).where(Order.session_id == session_id).order_by(Order.issued_at.desc())
    )
    orders = result.scalars().all()
    return [
        {
            "id": str(o.id),
            "issued_by_side": o.issued_by_side,
            "target_unit_ids": o.target_unit_ids,
            "order_type": o.order_type,
            "original_text": o.original_text,
            "status": o.status,
            "issued_at": o.issued_at.isoformat() if o.issued_at else None,
        }
        for o in orders
    ]


# ══════════════════════════════════════════════════════
# ── Grid Management ──────────────────────────────────
# ══════════════════════════════════════════════════════

class AdminGridUpdate(BaseModel):
    origin_lat: float
    origin_lon: float
    orientation_deg: float = 0.0
    base_square_size_m: float = 1000.0
    columns: int = 8
    rows: int = 8
    labeling_scheme: str = "alphanumeric"


@router.api_route("/sessions/{session_id}/grid", methods=["PUT", "POST"])
async def admin_update_grid(
    session_id: uuid.UUID, body: AdminGridUpdate, db: DB, user: CurrentUser,
):
    """Admin: update grid definition for a session."""
    from geoalchemy2.shape import from_shape
    from shapely.geometry import Point as ShapelyPoint

    # Clamp grid dimensions
    columns = max(1, min(20, body.columns))
    rows = max(1, min(20, body.rows))
    base_square_size_m = max(100, min(10000, body.base_square_size_m))

    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete existing grid definition
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))

    # Create new grid definition
    grid_def = GridDefinition(
        session_id=session_id,
        origin=from_shape(ShapelyPoint(body.origin_lon, body.origin_lat), srid=4326),
        orientation_deg=body.orientation_deg,
        base_square_size_m=base_square_size_m,
        columns=columns,
        rows=rows,
        labeling_scheme=body.labeling_scheme,
    )
    db.add(grid_def)
    await db.flush()

    return {
        "id": str(grid_def.id),
        "origin_lat": body.origin_lat,
        "origin_lon": body.origin_lon,
        "columns": columns,
        "rows": rows,
        "base_square_size_m": base_square_size_m,
    }


