"""Admin API endpoints – god-view, unit CRUD, participants, event injection, DB stats, user management."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import asyncio
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


async def _remove_user_from_unit_assignments(user_id: uuid.UUID, db):
    """Remove a user ID from all Unit.assigned_user_ids JSONB arrays."""
    user_id_str = str(user_id)
    result = await db.execute(
        select(Unit).where(Unit.assigned_user_ids.isnot(None))
    )
    units = result.scalars().all()
    for unit in units:
        if unit.assigned_user_ids and user_id_str in unit.assigned_user_ids:
            new_ids = [uid for uid in unit.assigned_user_ids if uid != user_id_str]
            unit.assigned_user_ids = new_ids if new_ids else None


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
    """Admin: delete a user and all their dependent data."""
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    # Remove FK references before deleting user
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.author_user_id == user_id))
    from sqlalchemy import update as sa_update
    await db.execute(sa_update(Order).where(Order.issued_by_user_id == user_id).values(issued_by_user_id=None))
    await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.user_id == user_id))
    # Clean up assigned_user_ids in units (JSONB array containing user ID)
    await _remove_user_from_unit_assignments(user_id, db)
    await db.delete(user)
    await db.flush()


class AdminBulkDelete(BaseModel):
    user_ids: list[str]


@router.post("/users/bulk-delete", status_code=200)
async def admin_bulk_delete_users(body: AdminBulkDelete, db: DB):
    """Admin: bulk delete multiple users and their dependent data."""
    from sqlalchemy import update as sa_update
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
        await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.author_user_id == uid))
        await db.execute(sa_update(Order).where(Order.issued_by_user_id == uid).values(issued_by_user_id=None))
        await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.user_id == uid))
        await _remove_user_from_unit_assignments(uid, db)
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

    # Enforce: observer side can only have observer role
    role = body.role or "observer"
    if side == Side.observer and role in ("commander", "officer"):
        role = "observer"

    participant = SessionParticipant(
        session_id=session_id,
        user_id=uid,
        side=side,
        role=role,
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
    comms_status: str | None = None
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
        select(Session).options(selectinload(Session.scenario)).order_by(Session.created_at.desc())
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
            "name": s.name or (s.scenario.title if s.scenario else None),
        })
    return out


class AdminSessionCreate(BaseModel):
    scenario_id: str
    settings: dict | None = None


class AdminSessionUpdate(BaseModel):
    name: str | None = None
    current_time: str | None = None  # ISO datetime string for operation start time
    settings: dict | None = None     # Session settings (turn_limit, etc.)


@router.post("/sessions")
async def admin_create_session(body: AdminSessionCreate, db: DB):
    """Admin: create a new session from a scenario (no auto-join).
    Immediately initializes grid and units from scenario data so they are
    available in lobby state for hierarchy setup and grid viewing."""
    result = await db.execute(select(Scenario).where(Scenario.id == uuid.UUID(body.scenario_id)))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    from backend.api.sessions import _get_scenario_start_time

    session = Session(
        scenario_id=scenario.id,
        name=scenario.title,  # default name from scenario
        status=SessionStatus.lobby,
        tick=0,
        tick_interval=60,
        current_time=_get_scenario_start_time(scenario) or datetime.now(timezone.utc),
        settings=body.settings,
    )
    db.add(session)
    await db.flush()

    # Auto-initialize grid and units from scenario (available in lobby)
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, scenario, db)

    return {
        "id": str(session.id),
        "scenario_id": str(session.scenario_id),
        "status": session.status.value,
        "tick": session.tick,
        "created_at": session.created_at.isoformat() if session.created_at else None,
        "name": session.name or scenario.title,
    }


@router.put("/sessions/{session_id}")
async def admin_update_session(session_id: uuid.UUID, body: AdminSessionUpdate, db: DB):
    """Admin: update session properties (e.g. rename)."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.name is not None:
        session.name = body.name.strip() if body.name.strip() else None
    if body.current_time is not None:
        try:
            session.current_time = datetime.fromisoformat(body.current_time.replace('Z', '+00:00'))
        except (ValueError, TypeError):
            raise HTTPException(status_code=400, detail="Invalid datetime format")
    if body.settings is not None:
        # Merge with existing settings (don't overwrite other keys)
        existing = session.settings or {}
        existing.update(body.settings)
        session.settings = existing
    await db.flush()

    # Resolve scenario title for response
    scenario_title = None
    if session.scenario_id:
        sc_result = await db.execute(select(Scenario).where(Scenario.id == session.scenario_id))
        sc = sc_result.scalar_one_or_none()
        if sc:
            scenario_title = sc.title
    return {
        "id": str(session.id),
        "name": session.name or scenario_title,
        "status": session.status.value,
    }


@router.delete("/sessions/{session_id}", status_code=204)
async def admin_delete_session(session_id: uuid.UUID, db: DB):
    """Admin: delete a single session and all its dependent data."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete all children first to avoid FK violations
    await db.execute(sa_delete(LocationReference).where(LocationReference.session_id == session_id))
    await db.execute(sa_delete(Event).where(Event.session_id == session_id))
    await db.execute(sa_delete(Report).where(Report.session_id == session_id))
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(Order).where(Order.session_id == session_id))
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))
    await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.session_id == session_id))
    await db.delete(session)
    await db.flush()


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
    if body.comms_status is not None:
        unit.comms_status = body.comms_status
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
        # Validate: observers cannot be assigned to units
        # Validate: side matching — blue commanders to blue units only, red to red
        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
        if body.assigned_user_ids:
            for uid_str in body.assigned_user_ids:
                try:
                    uid = uuid.UUID(uid_str)
                except ValueError:
                    continue
                part_result = await db.execute(
                    select(SessionParticipant).where(
                        SessionParticipant.session_id == session_id,
                        SessionParticipant.user_id == uid,
                    )
                )
                participant = part_result.scalar_one_or_none()
                if participant and (participant.side == Side.observer or participant.role == "observer"):
                    raise HTTPException(
                        status_code=400,
                        detail=f"Cannot assign observer to unit — observers do not control units"
                    )
                # Side matching: participant side must match unit side (admin side can assign to any)
                if participant:
                    p_side = participant.side.value if hasattr(participant.side, 'value') else str(participant.side)
                    if p_side not in ('admin',) and p_side != unit_side:
                        raise HTTPException(
                            status_code=400,
                            detail=f"Cannot assign {p_side} commander to {unit_side} unit — side mismatch"
                        )
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
    from sqlalchemy import update as sa_update, delete as sa_delete
    from backend.models.event import Event
    from backend.models.contact import Contact
    from backend.models.report import Report

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # ── Clean up FK references to this unit before deletion ──

    # Events: null out actor/target references (events are append-only, keep rows)
    await db.execute(
        sa_update(Event).where(Event.actor_unit_id == unit_id).values(actor_unit_id=None)
    )
    await db.execute(
        sa_update(Event).where(Event.target_unit_id == unit_id).values(target_unit_id=None)
    )

    # Contacts: null out observing_unit_id FK; also clean up contacts tracking this unit
    await db.execute(
        sa_update(Contact).where(Contact.observing_unit_id == unit_id).values(observing_unit_id=None)
    )
    await db.execute(
        sa_delete(Contact).where(Contact.target_unit_id == unit_id)
    )

    # Reports: null out from_unit_id
    await db.execute(
        sa_update(Report).where(Report.from_unit_id == unit_id).values(from_unit_id=None)
    )

    # Child units: re-parent to this unit's parent (or null)
    await db.execute(
        sa_update(Unit).where(Unit.parent_unit_id == unit_id).values(parent_unit_id=unit.parent_unit_id)
    )

    await db.delete(unit)
    await db.commit()


@router.delete("/sessions/{session_id}/units", status_code=200)
async def admin_delete_all_units(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: delete ALL units from a session."""
    from sqlalchemy import update as sa_update

    # Verify session exists
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Count units first
    count_result = await db.execute(
        select(func.count()).select_from(Unit).where(Unit.session_id == session_id)
    )
    unit_count = count_result.scalar() or 0
    if unit_count == 0:
        return {"deleted": 0, "message": "No units to delete"}

    # Clean up FK references before bulk deletion
    # Events: null out actor/target references
    await db.execute(
        sa_update(Event).where(Event.session_id == session_id, Event.actor_unit_id.isnot(None)).values(actor_unit_id=None)
    )
    await db.execute(
        sa_update(Event).where(Event.session_id == session_id, Event.target_unit_id.isnot(None)).values(target_unit_id=None)
    )
    # Contacts: delete all for session
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    # Reports: null out from_unit_id
    await db.execute(
        sa_update(Report).where(Report.session_id == session_id, Report.from_unit_id.isnot(None)).values(from_unit_id=None)
    )
    # Delete all units
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.commit()

    return {"deleted": unit_count, "message": f"Deleted {unit_count} units"}


@router.get("/sessions/{session_id}/units/{unit_id}/viewshed")
async def admin_get_unit_viewshed(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: DB,
    user: CurrentUser,
    rays: int = 72,
    step: float | None = None,
):
    """Admin viewshed endpoint — no participant check required."""
    # Reuse the regular viewshed logic from units.py
    from backend.api.units import _compute_viewshed
    return await _compute_viewshed(session_id, unit_id, db, rays, step)


class AdminSplitRequest(BaseModel):
    ratio: float = 0.5


class AdminMergeRequest(BaseModel):
    merge_with_unit_id: str


@router.post("/sessions/{session_id}/units/{unit_id}/split")
async def admin_split_unit(session_id: uuid.UUID, unit_id: uuid.UUID, body: AdminSplitRequest, db: DB, user: CurrentUser):
    """Admin: split a unit without authority check."""
    from backend.api.units import (
        get_current_echelon, echelon_one_down, get_principal_type,
        make_unit_type, update_sidc_echelon,
    )
    from backend.services.visibility_service import _serialize_unit
    from geoalchemy2.shape import to_shape, from_shape
    from shapely.geometry import Point as ShapelyPoint

    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    ratio = max(0.1, min(0.9, body.ratio))
    base_name = unit.name
    if "/" in base_name:
        base_name = base_name.rsplit("/", 1)[0]

    # Find next available suffix numbers
    existing_names = set()
    siblings = await db.execute(
        select(Unit.name).where(
            Unit.session_id == session_id, Unit.is_destroyed == False,
            Unit.name.like(f"{base_name}/%"),
        )
    )
    for (n,) in siblings:
        existing_names.add(n)

    num = 1
    name_a = f"{base_name}/{num}"
    while name_a in existing_names:
        num += 1
        name_a = f"{base_name}/{num}"
    existing_names.add(name_a)
    num += 1
    name_b = f"{base_name}/{num}"

    current_echelon = get_current_echelon(unit.sidc)
    new_echelon = echelon_one_down(current_echelon)
    principal = get_principal_type(unit.unit_type)
    new_unit_type = make_unit_type(principal, new_echelon)
    new_sidc = update_sidc_echelon(unit.sidc, new_echelon)

    position_copy = None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            position_copy = from_shape(ShapelyPoint(pt.x + 0.0004, pt.y), srid=4326)
        except Exception:
            pass

    new_unit = Unit(
        session_id=session_id, side=unit.side, name=name_b,
        unit_type=new_unit_type, sidc=new_sidc, parent_unit_id=unit.parent_unit_id,
        position=position_copy, heading_deg=unit.heading_deg,
        strength=unit.strength * ratio, ammo=unit.ammo, morale=unit.morale,
        suppression=unit.suppression, comms_status=unit.comms_status,
        capabilities=dict(unit.capabilities) if unit.capabilities else None,
        move_speed_mps=unit.move_speed_mps, detection_range_m=unit.detection_range_m,
        assigned_user_ids=list(unit.assigned_user_ids) if unit.assigned_user_ids else None,
    )
    db.add(new_unit)

    unit.name = name_a
    unit.strength = unit.strength * (1 - ratio)
    unit.unit_type = new_unit_type
    unit.sidc = new_sidc

    await db.flush()
    await db.refresh(new_unit)
    return {"original": _serialize_unit(unit), "new_unit": _serialize_unit(new_unit)}


@router.post("/sessions/{session_id}/units/{unit_id}/merge")
async def admin_merge_unit(session_id: uuid.UUID, unit_id: uuid.UUID, body: AdminMergeRequest, db: DB, user: CurrentUser):
    """Admin: merge two units without authority/distance check. Units must be same side/type."""
    from backend.api.units import (
        get_current_echelon, echelon_one_up, get_principal_type,
        make_unit_type, update_sidc_echelon, get_unit_latlon, haversine_m,
    )
    from backend.services.visibility_service import _serialize_unit

    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    survivor = result.scalar_one_or_none()
    if survivor is None:
        raise HTTPException(status_code=404, detail="Surviving unit not found")

    try:
        merge_uid = uuid.UUID(body.merge_with_unit_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid merge_with_unit_id")

    result2 = await db.execute(select(Unit).where(Unit.id == merge_uid, Unit.session_id == session_id))
    absorbed = result2.scalar_one_or_none()
    if absorbed is None:
        raise HTTPException(status_code=404, detail="Unit to merge not found")

    surv_principal = get_principal_type(survivor.unit_type)
    abs_principal = get_principal_type(absorbed.unit_type)
    if surv_principal != abs_principal:
        raise HTTPException(status_code=400, detail=f"Cannot merge different types ({surv_principal} vs {abs_principal})")
    if survivor.side != absorbed.side:
        raise HTTPException(status_code=400, detail="Cannot merge units from different sides")
    if str(survivor.id) == str(absorbed.id):
        raise HTTPException(status_code=400, detail="Cannot merge a unit with itself")

    # No distance restriction for admin merge

    total_str = survivor.strength + absorbed.strength
    w_surv = survivor.strength / total_str if total_str > 0 else 0.5
    w_abs = absorbed.strength / total_str if total_str > 0 else 0.5

    survivor.strength = min(1.0, total_str)
    survivor.ammo = min(1.0, survivor.ammo * w_surv + absorbed.ammo * w_abs)
    survivor.morale = min(1.0, survivor.morale * w_surv + absorbed.morale * w_abs)
    survivor.suppression = max(0.0, survivor.suppression * w_surv + absorbed.suppression * w_abs)

    child_result = await db.execute(select(Unit).where(Unit.parent_unit_id == absorbed.id))
    for child in child_result.scalars().all():
        child.parent_unit_id = survivor.id

    absorbed.is_destroyed = True
    absorbed.strength = 0.0
    absorbed.current_task = None

    name = survivor.name
    if "/" in name:
        name = name.rsplit("/", 1)[0]
    survivor.name = name

    current_echelon = get_current_echelon(survivor.sidc)
    new_echelon = echelon_one_up(current_echelon)
    principal = get_principal_type(survivor.unit_type)
    survivor.unit_type = make_unit_type(principal, new_echelon)
    survivor.sidc = update_sidc_echelon(survivor.sidc, new_echelon)

    await db.flush()
    return {"survivor": _serialize_unit(survivor), "absorbed_id": str(absorbed.id)}


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

    # Enforce: observer side can only have observer role
    if p.side == Side.observer and p.role in ("commander", "officer"):
        p.role = "observer"

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


class SetSessionTimeRequest(BaseModel):
    current_time: str  # ISO 8601 datetime string


@router.put("/sessions/{session_id}/set-time")
async def admin_set_session_time(session_id: uuid.UUID, body: SetSessionTimeRequest, db: DB, user: CurrentUser):
    """Admin: override the session's current game clock time."""
    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    try:
        new_time = datetime.fromisoformat(body.current_time.replace('Z', '+00:00'))
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Invalid datetime format. Use ISO 8601.")
    session.current_time = new_time
    await db.flush()
    return {
        "current_time": session.current_time.isoformat(),
        "tick": session.tick,
    }


async def _warm_session_caches(session_id: uuid.UUID, db):
    """Pre-warm in-memory caches (terrain, pathfinding graph, elevation peaks)
    so the first order after reset doesn't suffer a 10-15s cold-cache penalty.
    Called after session reset / resync / apply-scenario."""
    import logging as _wlog
    import time as _wtime
    _t0 = _wtime.monotonic()
    sid = str(session_id)
    try:
        from backend.models.terrain_cell import TerrainCell
        from backend.models.elevation_cell import ElevationCell
        from backend.engine.terrain import set_cached_terrain_data

        # 1. Load terrain + elevation cells into memory cache
        tc_result = await db.execute(
            select(TerrainCell.snail_path, TerrainCell.terrain_type)
            .where(TerrainCell.session_id == session_id)
        )
        tc_rows = tc_result.all()
        terrain_cells = {row[0]: row[1] for row in tc_rows} if tc_rows else None

        elevation_cells = None
        if tc_rows:
            ec_result = await db.execute(
                select(
                    ElevationCell.snail_path,
                    ElevationCell.elevation_m,
                    ElevationCell.slope_deg,
                    ElevationCell.aspect_deg,
                ).where(ElevationCell.session_id == session_id)
            )
            ec_rows = ec_result.all()
            if ec_rows:
                elevation_cells = {
                    row[0]: {"elevation_m": row[1], "slope_deg": row[2], "aspect_deg": row[3]}
                    for row in ec_rows
                }

        set_cached_terrain_data(sid, terrain_cells, elevation_cells)

        # 2. Pre-build pathfinding graph
        if terrain_cells:
            gd_result = await db.execute(
                select(GridDefinition).where(GridDefinition.session_id == session_id)
            )
            gd = gd_result.scalar_one_or_none()
            if gd:
                from backend.services.grid_service import GridService
                from backend.services.pathfinding_service import load_or_build_static_graph
                grid_service = GridService(gd)
                gd_settings = dict(gd.settings_json) if gd.settings_json else None
                load_or_build_static_graph(
                    sid, terrain_cells, elevation_cells,
                    None, grid_service,
                    grid_def_settings_json=gd_settings,
                )

        # 3. Pre-warm elevation peaks cache
        try:
            from backend.api.terrain import get_elevation_peaks_cached
            await get_elevation_peaks_cached(session_id, db)
        except Exception:
            pass

        _elapsed = _wtime.monotonic() - _t0
        _wlog.getLogger(__name__).info(
            "Cache warm-up for session %s: %.1fs (terrain=%s cells, elev=%s cells)",
            session_id, _elapsed,
            len(terrain_cells) if terrain_cells else 0,
            len(elevation_cells) if elevation_cells else 0,
        )
    except Exception as e:
        _wlog.getLogger(__name__).warning("Cache warm-up failed for %s: %s", session_id, e)


@router.post("/sessions/{session_id}/reset")
async def admin_reset_session(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: reset session to tick 0, re-create units from scenario.
    Preserves: map objects, terrain cells, elevation cells, planning overlays.
    Clears: units, orders, events, reports, contacts, chat, red agents, grid.
    """
    from backend.models.chat_message import ChatMessage

    result = await db.execute(
        select(Session).options(selectinload(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    # Delete game-state data — but PRESERVE map objects, terrain cells, elevation cells
    await db.execute(sa_delete(LocationReference).where(LocationReference.session_id == session_id))
    await db.execute(sa_delete(Event).where(Event.session_id == session_id))
    await db.execute(sa_delete(Report).where(Report.session_id == session_id))
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(Order).where(Order.session_id == session_id))
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))

    # Reset session state
    from backend.api.sessions import _get_scenario_start_time
    session.tick = 0
    session.status = SessionStatus.lobby
    session.current_time = _get_scenario_start_time(session.scenario) or datetime.now(timezone.utc)
    await db.flush()

    # Re-initialize from scenario
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, session.scenario, db)

    # Commit immediately so the frontend sees the reset state
    await db.commit()

    # Fire-and-forget cache warm-up in background (don't block the response)
    _sid = session_id
    async def _bg_warm():
        from backend.database import async_session_factory
        async with async_session_factory() as bg_db:
            try:
                await _warm_session_caches(_sid, bg_db)
            except Exception as e:
                import logging
                logging.getLogger(__name__).warning("Background cache warm-up failed: %s", e)
    asyncio.create_task(_bg_warm())

    return {"status": session.status.value, "tick": 0, "message": "Session reset to turn 0", "current_time": session.current_time.isoformat() if session.current_time else None}


@router.post("/sessions/{session_id}/resync-units")
async def admin_resync_units(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: resync session units and grid from the source scenario.
    
    Deletes existing units + grid and re-creates them from the scenario's
    initial_units / grid_settings. Preserves session status, tick, participants,
    events, overlays and other data. Used after editing a scenario via the builder.
    """
    from sqlalchemy.orm import selectinload as _sel
    result = await db.execute(
        select(Session).options(_sel(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.scenario is None:
        raise HTTPException(status_code=400, detail="Session has no linked scenario")

    # Delete existing units, grid and contacts (which reference units)
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))
    await db.flush()

    # Re-initialize from scenario
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, session.scenario, db)

    # Count new units
    unit_count_result = await db.execute(
        select(func.count()).select_from(Unit).where(Unit.session_id == session_id)
    )
    unit_count = unit_count_result.scalar() or 0

    # Pre-warm caches
    await _warm_session_caches(session_id, db)

    return {
        "status": session.status.value,
        "tick": session.tick,
        "units_created": unit_count,
        "message": f"Resynced {unit_count} units from scenario '{session.scenario.title}'",
    }


class ApplyScenarioRequest(BaseModel):
    scenario_id: str


@router.post("/sessions/{session_id}/apply-scenario")
async def admin_apply_scenario(session_id: uuid.UUID, body: ApplyScenarioRequest, db: DB, user: CurrentUser):
    """Admin: change the scenario for an active session. Resets units/grid."""
    from backend.models.chat_message import ChatMessage
    from backend.models.map_object import MapObject
    from backend.models.terrain_cell import TerrainCell
    from backend.models.elevation_cell import ElevationCell

    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        scenario_uuid = uuid.UUID(body.scenario_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scenario_id")

    sc_result = await db.execute(select(Scenario).where(Scenario.id == scenario_uuid))
    scenario = sc_result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Delete all existing game-state data for this session (comprehensive)
    await db.execute(sa_delete(LocationReference).where(LocationReference.session_id == session_id))
    await db.execute(sa_delete(Event).where(Event.session_id == session_id))
    await db.execute(sa_delete(Report).where(Report.session_id == session_id))
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(Order).where(Order.session_id == session_id))
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.execute(sa_delete(MapObject).where(MapObject.session_id == session_id))
    await db.execute(sa_delete(TerrainCell).where(TerrainCell.session_id == session_id))
    await db.execute(sa_delete(ElevationCell).where(ElevationCell.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))

    # Update session to point to new scenario
    from backend.api.sessions import _get_scenario_start_time as _gst2
    session.scenario_id = scenario_uuid
    session.tick = 0
    session.status = SessionStatus.lobby
    session.current_time = _gst2(scenario) or datetime.now(timezone.utc)
    await db.flush()

    # Re-initialize from new scenario
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, scenario, db)

    # Pre-warm caches
    await _warm_session_caches(session_id, db)

    return {
        "status": session.status.value,
        "tick": 0,
        "scenario_id": str(scenario_uuid),
        "scenario_title": scenario.title,
        "message": f"Scenario changed to '{scenario.title}'. Session reset to turn 0.",
    }


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



# ══════════════════════════════════════════════════════
# ── Admin Change Scenario for Session ────────────────
# ══════════════════════════════════════════════════════

class AdminChangeScenario(BaseModel):
    scenario_id: str


@router.post("/sessions/{session_id}/change-scenario")
async def admin_change_scenario(
    session_id: uuid.UUID, body: AdminChangeScenario, db: DB, user: CurrentUser,
):
    """Admin: change the scenario for a session, resetting units and grid."""
    from backend.models.chat_message import ChatMessage
    from backend.models.map_object import MapObject
    from backend.models.terrain_cell import TerrainCell
    from backend.models.elevation_cell import ElevationCell

    result = await db.execute(select(Session).where(Session.id == session_id))
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")

    try:
        scenario_uid = uuid.UUID(body.scenario_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid scenario_id")

    result2 = await db.execute(select(Scenario).where(Scenario.id == scenario_uid))
    scenario = result2.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")

    # Delete existing game-state data (comprehensive)
    await db.execute(sa_delete(LocationReference).where(LocationReference.session_id == session_id))
    await db.execute(sa_delete(Event).where(Event.session_id == session_id))
    await db.execute(sa_delete(Report).where(Report.session_id == session_id))
    await db.execute(sa_delete(Contact).where(Contact.session_id == session_id))
    await db.execute(sa_delete(Order).where(Order.session_id == session_id))
    await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id == session_id))
    await db.execute(sa_delete(RedAgent).where(RedAgent.session_id == session_id))
    await db.execute(sa_delete(ChatMessage).where(ChatMessage.session_id == session_id))
    await db.execute(sa_delete(MapObject).where(MapObject.session_id == session_id))
    await db.execute(sa_delete(TerrainCell).where(TerrainCell.session_id == session_id))
    await db.execute(sa_delete(ElevationCell).where(ElevationCell.session_id == session_id))
    await db.execute(sa_delete(Unit).where(Unit.session_id == session_id))
    await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id == session_id))

    # Update session
    from backend.api.sessions import _get_scenario_start_time as _gst3
    session.scenario_id = scenario_uid
    session.tick = 0
    session.status = SessionStatus.lobby
    session.current_time = _gst3(scenario) or datetime.now(timezone.utc)
    session.name = scenario.title
    await db.flush()

    # Re-initialize from new scenario
    from backend.services.session_service import initialize_session_from_scenario
    await initialize_session_from_scenario(session, scenario, db)

    return {
        "status": session.status.value,
        "tick": 0,
        "scenario_id": str(scenario_uid),
        "name": session.name,
        "message": "Scenario changed, units and grid reset",
    }


# ══════════════════════════════════════════════════════
# ── Save Session State to Scenario ───────────────────
# ══════════════════════════════════════════════════════

@router.post("/sessions/{session_id}/save-to-scenario")
async def admin_save_session_to_scenario(session_id: uuid.UUID, db: DB, user: CurrentUser):
    """Admin: overwrite the linked scenario with the current session state (units + grid).

    Snapshots all current units (positions, stats, types) and grid settings
    back into the scenario's initial_units and grid_settings fields.
    This allows the admin to tweak unit placement in a live session and
    save it as the new scenario baseline.
    """
    import math
    from geoalchemy2.shape import to_shape

    result = await db.execute(
        select(Session).options(selectinload(Session.scenario)).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=404, detail="Session not found")
    if session.scenario is None:
        raise HTTPException(status_code=400, detail="Session has no linked scenario")

    scenario = session.scenario

    # ── Snapshot grid settings ─────────────────────
    grid_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = grid_result.scalar_one_or_none()
    if grid_def:
        origin_point = to_shape(grid_def.origin) if grid_def.origin else None
        origin_lat = origin_point.y if origin_point else 0
        origin_lon = origin_point.x if origin_point else 0
        scenario.grid_settings = {
            "origin_lat": origin_lat,
            "origin_lon": origin_lon,
            "orientation_deg": grid_def.orientation_deg or 0,
            "base_square_size_m": grid_def.base_square_size_m or 1000,
            "columns": grid_def.columns or 8,
            "rows": grid_def.rows or 8,
            "labeling_scheme": grid_def.labeling_scheme or "alphanumeric",
        }
    else:
        origin_lat = 0
        origin_lon = 0

    # ── Snapshot units ─────────────────────────────
    units_result = await db.execute(
        select(Unit).where(Unit.session_id == session_id, Unit.is_destroyed == False)
    )
    units = units_result.scalars().all()

    blue_units = []
    red_units = []

    for u in units:
        unit_point = to_shape(u.position) if u.position else None
        lat = unit_point.y if unit_point else 0
        lon = unit_point.x if unit_point else 0

        # Compute grid-relative offsets from grid origin
        grid_offset_x = None
        grid_offset_y = None
        if origin_lat and origin_lon:
            lat_rad = math.radians(origin_lat)
            m_per_deg_lat = 111320.0
            m_per_deg_lon = 111320.0 * math.cos(lat_rad) if lat_rad else 111320.0
            grid_offset_x = (lon - origin_lon) * m_per_deg_lon
            grid_offset_y = (lat - origin_lat) * m_per_deg_lat

        unit_data = {
            "name": u.name,
            "unit_type": u.unit_type,
            "sidc": u.sidc or "",
            "lat": lat,
            "lon": lon,
            "grid_offset_x": grid_offset_x,
            "grid_offset_y": grid_offset_y,
            "strength": u.strength if u.strength is not None else 1.0,
            "ammo": u.ammo if u.ammo is not None else 1.0,
            "morale": u.morale if u.morale is not None else 0.9,
            "move_speed_mps": u.move_speed_mps or 4.0,
            "detection_range_m": u.detection_range_m or 1500,
            "capabilities": u.capabilities or {},
        }

        if u.side == "blue":
            blue_units.append(unit_data)
        else:
            red_units.append(unit_data)

    # Snapshot Red Agents
    ra_result = await db.execute(
        select(RedAgent).where(RedAgent.session_id == session_id)
    )
    red_agents = ra_result.scalars().all()
    red_agents_data = []
    for ra in red_agents:
        ra_data = {
            "name": ra.name,
            "doctrine_profile": ra.doctrine_profile,
            "mission_intent": ra.mission_intent,
            "risk_posture": ra.risk_posture if isinstance(ra.risk_posture, str) else (ra.risk_posture.value if ra.risk_posture else "balanced"),
            "controlled_units": [],
        }
        # Resolve controlled unit IDs to names
        if ra.controlled_unit_ids:
            for uid in ra.controlled_unit_ids:
                for u in units:
                    if str(u.id) == str(uid):
                        ra_data["controlled_units"].append(u.name)
                        break
        red_agents_data.append(ra_data)

    scenario.initial_units = {
        "blue": blue_units,
        "red": red_units,
        "red_agents": red_agents_data,
    }

    # Update map center from grid center if available
    if grid_def and origin_lat and origin_lon:
        from geoalchemy2.shape import from_shape
        from shapely.geometry import Point as ShapelyPoint
        gs = scenario.grid_settings
        center_lat = origin_lat + (gs["rows"] * gs["base_square_size_m"] / 2) / 111320
        center_lon = origin_lon + (gs["columns"] * gs["base_square_size_m"] / 2) / (111320 * math.cos(math.radians(origin_lat)))
        scenario.map_center = from_shape(ShapelyPoint(center_lon, center_lat), srid=4326)

    # Save session current_time as scenario start_time in environment
    if session.current_time:
        env = dict(scenario.environment or {})
        env["start_time"] = session.current_time.isoformat()
        scenario.environment = env

    # Persist elevation peaks cache in scenario terrain_meta for fast loading
    if grid_def and grid_def.settings_json and grid_def.settings_json.get("peaks_cache"):
        terrain_meta = dict(scenario.terrain_meta or {})
        terrain_meta["peaks_cache"] = grid_def.settings_json["peaks_cache"]
        scenario.terrain_meta = terrain_meta

    await db.flush()

    return {
        "scenario_id": str(scenario.id),
        "scenario_title": scenario.title,
        "blue_units": len(blue_units),
        "red_units": len(red_units),
        "red_agents": len(red_agents_data),
        "message": f"Scenario '{scenario.title}' updated with {len(blue_units)} blue + {len(red_units)} red units from current session.",
    }


# ══════════════════════════════════════════════════════
# ── Debug Logging ────────────────────────────────────
# ══════════════════════════════════════════════════════

@router.post("/debug-log/enable")
async def enable_debug_log():
    """Enable debug logging to file. Returns file path."""
    from backend.services.debug_logger import enable_debug_logging
    path = enable_debug_logging()
    return {"enabled": True, "path": path}


@router.post("/debug-log/disable")
async def disable_debug_log():
    """Disable debug logging."""
    from backend.services.debug_logger import disable_debug_logging
    disable_debug_logging()
    return {"enabled": False}


@router.get("/debug-log/status")
async def debug_log_status():
    """Get debug logging status."""
    from backend.services.debug_logger import is_debug_logging_enabled, _log_file_path
    return {
        "enabled": is_debug_logging_enabled(),
        "path": str(_log_file_path),
    }


@router.get("/debug-log/contents")
async def get_debug_log(tail: int = 300):
    """Get the last N lines of the debug log file."""
    from backend.services.debug_logger import get_log_contents
    return {"contents": get_log_contents(tail_lines=tail)}


@router.post("/debug-log/clear")
async def clear_debug_log():
    """Clear the debug log file."""
    from backend.services.debug_logger import clear_log
    clear_log()
    return {"cleared": True}

