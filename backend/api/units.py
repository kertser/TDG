"""Units API – fog-of-war filtered unit and contact retrieval, unit assignment,
hierarchy, split/merge, formation, movement commands."""

from __future__ import annotations

import uuid
from math import radians, cos, sin, asin, sqrt
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.api.deps import get_session_participant
from backend.services.visibility_service import (
    get_visible_units,
    get_visible_contacts,
    enrich_units_with_command_info,
    check_command_authority,
    check_subordinate_authority,
    _serialize_unit,
)
from backend.models.unit import Unit
from backend.models.session import SessionParticipant, Side

router = APIRouter()


# ══════════════════════════════════════════════════
# ── Unit Size / Echelon Utilities ─────────────────
# ══════════════════════════════════════════════════

# SIDC positions 8-9 echelon codes (MIL-STD-2525D)
ECHELON_HIERARCHY = ['11', '12', '13', '14', '15', '16', '17', '18']
ECHELON_NAMES = {
    '11': 'team', '12': 'squad', '13': 'section',
    '14': 'platoon', '15': 'company', '16': 'battalion',
    '17': 'regiment', '18': 'brigade',
}
ECHELON_TO_SUFFIX = {
    '11': 'team', '12': 'squad', '13': 'section',
    '14': 'platoon', '15': 'company', '16': 'battalion',
}

# Suffixes that indicate unit size in unit_type strings
SIZE_SUFFIXES = [
    '_battalion', '_company', '_battery', '_platoon',
    '_section', '_squad', '_team', '_post', '_unit',
]

BASE_PERSONNEL = {
    'headquarters': 20, 'command_post': 10,
    'infantry_platoon': 30, 'infantry_company': 120,
    'infantry_section': 15, 'infantry_team': 6,
    'tank_company': 60, 'tank_platoon': 15,
    'mech_company': 100, 'mech_platoon': 30,
    'artillery_battery': 40, 'artillery_platoon': 20,
    'mortar_section': 12, 'mortar_team': 6,
    'at_team': 6, 'recon_team': 6, 'recon_section': 12,
    'observation_post': 4, 'sniper_team': 2,
    'engineer_platoon': 30, 'engineer_section': 15,
    'logistics_unit': 20,
    'combat_engineer_platoon': 30, 'combat_engineer_section': 15, 'combat_engineer_team': 8,
    'mine_layer_section': 10, 'mine_layer_team': 5,
    'obstacle_breacher_team': 6, 'obstacle_breacher_section': 12,
    'engineer_recon_team': 4,
    'construction_engineer_platoon': 30, 'construction_engineer_section': 15,
    'avlb_vehicle': 4, 'avlb_section': 8,
}
DEFAULT_PERSONNEL = 20


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Haversine distance in meters between two lat/lon points."""
    R = 6_371_000
    lat1, lon1, lat2, lon2 = map(radians, [lat1, lon1, lat2, lon2])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = sin(dlat / 2) ** 2 + cos(lat1) * cos(lat2) * sin(dlon / 2) ** 2
    return 2 * R * asin(sqrt(a))


def get_principal_type(unit_type: str) -> str:
    """Extract principal/base type from unit_type (e.g. 'infantry_company' → 'infantry')."""
    for suffix in SIZE_SUFFIXES:
        if unit_type.endswith(suffix):
            return unit_type[: -len(suffix)]
    return unit_type


def get_current_echelon(sidc: str | None) -> str:
    """Extract echelon code (positions 8-9) from SIDC string."""
    if sidc and len(sidc) >= 10:
        return sidc[8:10]
    return '14'  # default platoon


def echelon_one_down(echelon: str) -> str:
    """Return echelon one level below the given one."""
    try:
        idx = ECHELON_HIERARCHY.index(echelon)
        return ECHELON_HIERARCHY[max(0, idx - 1)]
    except ValueError:
        return '14'


def echelon_one_up(echelon: str) -> str:
    """Return echelon one level above the given one."""
    try:
        idx = ECHELON_HIERARCHY.index(echelon)
        return ECHELON_HIERARCHY[min(len(ECHELON_HIERARCHY) - 1, idx + 1)]
    except ValueError:
        return '15'


def update_sidc_echelon(sidc: str | None, echelon_code: str) -> str | None:
    """Return a new SIDC string with positions 8-9 updated to the given echelon."""
    if not sidc or len(sidc) < 20:
        return sidc
    return sidc[:8] + echelon_code + sidc[10:]


def make_unit_type(principal: str, echelon_code: str) -> str:
    """Build a unit_type string from principal type and echelon code."""
    # Special cases
    if principal == 'artillery' and echelon_code == '15':
        return 'artillery_battery'
    if principal == 'observation':
        return 'observation_post'
    if principal == 'logistics':
        return 'logistics_unit'
    if principal in ('headquarters', 'command_post'):
        return principal
    suffix = ECHELON_TO_SUFFIX.get(echelon_code, 'platoon')
    return f'{principal}_{suffix}'


def get_unit_latlon(unit) -> tuple[float | None, float | None]:
    """Extract lat/lon from a Unit model's PostGIS position."""
    if unit.position is None:
        return None, None
    try:
        from geoalchemy2.shape import to_shape
        pt = to_shape(unit.position)
        return pt.y, pt.x
    except Exception:
        return None, None


class UnitAssignRequest(BaseModel):
    assigned_user_ids: list[str]


class UnitRenameRequest(BaseModel):
    name: str


class UnitFormationRequest(BaseModel):
    formation: str


class UnitMoveRequest(BaseModel):
    target_lat: float
    target_lon: float
    speed: str = "slow"


class UnitSplitRequest(BaseModel):
    ratio: float = 0.5
    new_name: str | None = None  # optional override; auto-generated if not provided


class UnitMergeRequest(BaseModel):
    merge_with_unit_id: str


@router.get("/{session_id}/units")
async def get_units(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return units visible to the requester's side (fog-of-war filtered),
    enriched with commanding officer info."""
    side = participant.side.value
    role = getattr(participant, 'role', None)
    # Observer and admin see everything
    if side in ("admin", "observer") or role == "observer":
        vis_side = "observer"
    elif side in ("blue", "red"):
        vis_side = side
    else:
        vis_side = "blue"
    units = await get_visible_units(session_id, vis_side, db)
    units = await enrich_units_with_command_info(units, session_id, db, requesting_side=vis_side)
    return units


@router.get("/{session_id}/units/hierarchy")
async def get_unit_hierarchy(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return unit hierarchy with command info for the user's side."""
    side = participant.side.value
    if side in ("admin", "observer"):
        result = await db.execute(
            select(Unit).where(Unit.session_id == session_id, Unit.is_destroyed == False)
        )
    else:
        result = await db.execute(
            select(Unit).where(Unit.session_id == session_id, Unit.side == side, Unit.is_destroyed == False)
        )
    units = result.scalars().all()
    serialized = [_serialize_unit(u) for u in units]
    enriched = await enrich_units_with_command_info(serialized, session_id, db, requesting_side=side)
    return enriched


@router.get("/{session_id}/units/{unit_id}")
async def get_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    side = participant.side.value
    units = await get_visible_units(session_id, side, db)
    for u in units:
        if u["id"] == str(unit_id):
            return u
    raise HTTPException(status_code=404, detail="Unit not found or not visible")


@router.put("/{session_id}/units/{unit_id}/rename")
async def rename_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitRenameRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    side = participant.side.value
    user_id = str(participant.user_id)
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    # Admin-side participants and X-Admin-Mode header bypass side checks
    is_admin_mode = side == "admin" or request.headers.get("x-admin-mode") == "1"
    if not is_admin_mode and side not in ("observer",):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot rename units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")
    new_name = body.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="Name cannot be empty")
    unit.name = new_name
    await db.flush()
    return {"id": str(unit.id), "name": unit.name}


@router.put("/{session_id}/units/{unit_id}/assign")
async def assign_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitAssignRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    side = participant.side.value
    user_id = str(participant.user_id)
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if side == "observer" or participant.role == "observer":
        raise HTTPException(status_code=403, detail="Observers cannot assign units")
    if side == "admin":
        pass
    else:
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot assign units from other side")
        current_owners = unit.assigned_user_ids or []
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            if len(current_owners) > 0 and user_id not in current_owners:
                raise HTTPException(status_code=403, detail="Only commanders, the unit owner, admin, or referee can assign this unit")
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
            part = part_result.scalar_one_or_none()
            if part and (part.side == Side.observer or part.role == "observer"):
                raise HTTPException(status_code=400, detail="Cannot assign observer to unit")
    unit.assigned_user_ids = body.assigned_user_ids if body.assigned_user_ids else None
    await db.flush()
    return {"id": str(unit.id), "assigned_user_ids": unit.assigned_user_ids}


# ══════════════════════════════════════════════════
# ── Parent / Hierarchy (for commanders) ───────────
# ══════════════════════════════════════════════════

class UnitSetParentRequest(BaseModel):
    parent_unit_id: str | None = None


@router.put("/{session_id}/units/{unit_id}/parent")
async def set_unit_parent(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitSetParentRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Set or clear a unit's parent in the chain of command.
    Commanders can reparent units they have authority over."""
    side = participant.side.value
    user_id = str(participant.user_id)

    # Observers cannot modify hierarchy
    if side == "observer" or participant.role == "observer":
        raise HTTPException(status_code=403, detail="Observers cannot modify hierarchy")

    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    # Authority check (skip for admin)
    if side != "admin":
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot modify units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            # Check if no CoC is set up (implicit authority)
            all_result = await db.execute(
                select(Unit).where(Unit.session_id == session_id, Unit.side == unit.side.value)
            )
            all_same_side = all_result.scalars().all()
            any_assigned = any(u.assigned_user_ids for u in all_same_side)
            if any_assigned:
                raise HTTPException(status_code=403, detail="No command authority over this unit")

    if body.parent_unit_id:
        try:
            parent_uuid = uuid.UUID(body.parent_unit_id)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid parent_unit_id")
        if parent_uuid == unit_id:
            raise HTTPException(status_code=400, detail="Unit cannot be its own parent")
        # Verify parent exists in same session and same side
        pr = await db.execute(
            select(Unit).where(Unit.id == parent_uuid, Unit.session_id == session_id)
        )
        parent = pr.scalar_one_or_none()
        if not parent:
            raise HTTPException(status_code=404, detail="Parent unit not found")
        if parent.side != unit.side:
            raise HTTPException(status_code=400, detail="Cannot set parent from different side")
        # Cycle detection: walk up from parent to ensure we don't create a loop
        current = parent
        visited = {unit_id}
        while current.parent_unit_id:
            if current.parent_unit_id in visited:
                raise HTTPException(status_code=400, detail="Circular hierarchy detected")
            visited.add(current.parent_unit_id)
            pr2 = await db.execute(select(Unit).where(Unit.id == current.parent_unit_id))
            current = pr2.scalar_one_or_none()
            if not current:
                break
        unit.parent_unit_id = parent_uuid
    else:
        unit.parent_unit_id = None

    await db.flush()
    return {"id": str(unit.id), "parent_unit_id": str(unit.parent_unit_id) if unit.parent_unit_id else None}


# ══════════════════════════════════════════════════
# ── Formation ─────────────────────────────────────
# ══════════════════════════════════════════════════

VALID_FORMATIONS = {
    "column", "line", "wedge", "vee", "echelon_left", "echelon_right",
    "staggered", "box", "diamond", "dispersed", "herringbone",
}


@router.put("/{session_id}/units/{unit_id}/formation")
async def set_unit_formation(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitFormationRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    side = participant.side.value
    user_id = str(participant.user_id)
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot modify units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")
    if body.formation not in VALID_FORMATIONS:
        raise HTTPException(status_code=400, detail=f"Invalid formation. Valid: {', '.join(sorted(VALID_FORMATIONS))}")
    caps = unit.capabilities or {}
    caps["formation"] = body.formation
    unit.capabilities = caps
    await db.flush()
    return {"id": str(unit.id), "formation": body.formation}


# ══════════════════════════════════════════════════
# ── Movement Command ──────────────────────────────
# ══════════════════════════════════════════════════

# Unit-type-specific base speeds (m/s) for slow and fast movement.
# Terrain modifiers are applied at runtime by the engine.
# slow  = cautious/tactical movement (better concealment, less fatigue)
# fast  = rapid movement (exposed, tiring, vehicles at higher speed)
UNIT_TYPE_SPEEDS: dict[str, dict[str, float]] = {
    # Foot infantry
    "infantry_platoon":  {"slow": 1.2, "fast": 3.0},    # ~4 km/h / ~11 km/h
    "infantry_company":  {"slow": 1.0, "fast": 2.5},    # ~4 km/h / ~9 km/h (larger = slower)
    "infantry_section":  {"slow": 1.2, "fast": 3.0},
    "infantry_team":     {"slow": 1.5, "fast": 3.5},    # small team = faster
    "infantry_squad":    {"slow": 1.2, "fast": 3.0},
    "infantry_battalion": {"slow": 0.8, "fast": 2.0},   # large formation
    # Mechanized / motorized
    "mech_platoon":      {"slow": 3.0, "fast": 10.0},   # ~11 km/h / ~36 km/h
    "mech_company":      {"slow": 2.5, "fast": 8.0},
    # Armor
    "tank_platoon":      {"slow": 3.0, "fast": 12.0},   # ~11 km/h / ~43 km/h
    "tank_company":      {"slow": 2.5, "fast": 10.0},
    # Artillery
    "artillery_battery": {"slow": 1.5, "fast": 5.0},
    "artillery_platoon": {"slow": 1.5, "fast": 5.0},
    # Support / light
    "mortar_section":    {"slow": 1.0, "fast": 2.5},    # heavy load
    "mortar_team":       {"slow": 1.2, "fast": 3.0},
    "at_team":           {"slow": 1.2, "fast": 3.0},
    "recon_team":        {"slow": 2.0, "fast": 4.0},    # scouts are faster
    "recon_section":     {"slow": 2.0, "fast": 4.0},
    "sniper_team":       {"slow": 1.0, "fast": 2.5},    # stealthy
    "observation_post":  {"slow": 0.5, "fast": 1.5},    # rarely moves
    "engineer_platoon":  {"slow": 1.0, "fast": 2.5},
    "engineer_section":  {"slow": 1.0, "fast": 2.5},
    "logistics_unit":    {"slow": 2.0, "fast": 6.0},    # trucks
    "headquarters":      {"slow": 1.5, "fast": 5.0},
    "command_post":      {"slow": 1.0, "fast": 3.0},
    # Engineering units
    "combat_engineer_platoon":  {"slow": 1.2, "fast": 3.0},
    "combat_engineer_section":  {"slow": 1.2, "fast": 3.0},
    "combat_engineer_team":     {"slow": 1.5, "fast": 3.5},
    "mine_layer_section":       {"slow": 1.0, "fast": 2.5},
    "mine_layer_team":          {"slow": 1.2, "fast": 3.0},
    "obstacle_breacher_team":   {"slow": 1.2, "fast": 3.0},
    "obstacle_breacher_section": {"slow": 1.0, "fast": 2.5},
    "engineer_recon_team":      {"slow": 2.0, "fast": 4.0},
    "construction_engineer_platoon": {"slow": 0.8, "fast": 2.0},
    "construction_engineer_section": {"slow": 0.8, "fast": 2.0},
    "avlb_vehicle":             {"slow": 2.0, "fast": 6.0},   # armored vehicle
    "avlb_section":             {"slow": 2.0, "fast": 6.0},
}
DEFAULT_SPEEDS = {"slow": 1.2, "fast": 3.0}  # fallback for unknown types

VALID_SPEED_LABELS = {"slow", "fast"}

# ── Unit-type-specific eye heights (meters above ground) for LOS / viewshed ──
# Observation posts have elevated optics; vehicles have turret height; infantry is standing height.
UNIT_EYE_HEIGHTS: dict[str, float] = {
    "observation_post":   8.0,    # elevated observation platform / optics on mast
    "tank_company":       3.0,    # turret height
    "tank_platoon":       3.0,
    "mech_company":       2.8,    # IFV turret
    "mech_platoon":       2.8,
    "recon_team":         3.0,    # optics on vehicle or elevated position
    "recon_section":      3.0,
    "sniper_team":        2.5,    # often on elevated positions
    "headquarters":       3.0,    # command vehicle
    "command_post":       3.0,
    "artillery_battery":  2.5,
    "artillery_platoon":  2.5,
}
DEFAULT_EYE_HEIGHT = 2.0  # infantry standing height


@router.put("/{session_id}/units/{unit_id}/move")
async def set_unit_move(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitMoveRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Queue a move order for the unit. The order is executed on the next tick.

    Creates an Order record with status=validated. The tick engine picks it up
    in _process_orders, assigns the task to the unit, and movement begins.
    The unit acknowledges the order immediately but does NOT move until the tick.
    """
    from backend.models.order import Order, OrderStatus, OrderSide

    side = participant.side.value
    user_id = str(participant.user_id)
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot command units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")
    speed_label = body.speed.lower()
    # Accept "average" as alias for backwards compatibility, map to "slow"
    if speed_label == "average":
        speed_label = "slow"
    if speed_label not in VALID_SPEED_LABELS:
        raise HTTPException(status_code=400, detail="Invalid speed. Valid: slow, fast")

    # Resolve target coordinates to snail path for display
    target_snail = None
    try:
        from backend.models.grid import GridDefinition
        grid_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        grid_def = grid_result.scalar_one_or_none()
        if grid_def:
            from backend.services.grid_service import GridService
            grid_svc = GridService(grid_def)
            target_snail = grid_svc.point_to_snail(body.target_lat, body.target_lon, depth=2)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Snail path resolution failed: %s", e)

    # Build the engine-ready task as parsed_order
    task_data = {
        "type": "move",
        "target_location": {"lat": body.target_lat, "lon": body.target_lon},
        "target_snail": target_snail,
        "speed": speed_label,
        "source": "ui_move",
    }

    # Create an Order record — the tick engine will process it
    unit_side_enum = OrderSide(unit.side.value if hasattr(unit.side, 'value') else unit.side)
    order = Order(
        session_id=session_id,
        issued_by_user_id=participant.user_id,
        issued_by_side=unit_side_enum,
        target_unit_ids=[unit_id],
        order_type="move",
        original_text=f"Move to {target_snail or f'{body.target_lat:.4f},{body.target_lon:.4f}'} ({speed_label})",
        parsed_order=task_data,
        status=OrderStatus.validated,
    )
    db.add(order)

    # Pre-set move_speed_mps so the engine has the correct speed when task is assigned
    speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
    unit.move_speed_mps = speeds[speed_label]

    await db.flush()

    # Return unit data + pending order info so frontend can show dashed arrow
    unit_data = _serialize_unit(unit)
    unit_data["pending_order"] = {
        "id": str(order.id),
        "type": "move",
        "target_location": {"lat": body.target_lat, "lon": body.target_lon},
        "target_snail": target_snail,
        "speed": speed_label,
        "status": "validated",
    }
    return unit_data


@router.put("/{session_id}/units/{unit_id}/stop")
async def stop_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Queue a halt order for the unit. Executed on the next tick.

    Creates an Order record with status=validated and type=halt.
    The tick engine clears the unit's current_task when processing it.
    """
    from backend.models.order import Order, OrderStatus, OrderSide

    side = participant.side.value
    user_id = str(participant.user_id)
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot command units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    # Create halt order
    unit_side_enum = OrderSide(unit.side.value if hasattr(unit.side, 'value') else unit.side)
    order = Order(
        session_id=session_id,
        issued_by_user_id=participant.user_id,
        issued_by_side=unit_side_enum,
        target_unit_ids=[unit_id],
        order_type="halt",
        original_text="Halt / All stop",
        parsed_order={"type": "halt", "source": "ui_stop"},
        status=OrderStatus.validated,
    )
    db.add(order)
    await db.flush()

    unit_data = _serialize_unit(unit)
    unit_data["pending_order"] = {
        "id": str(order.id),
        "type": "halt",
        "status": "validated",
    }
    return unit_data


# ══════════════════════════════════════════════════
# ── Split / Merge ─────────────────────────────────
# ══════════════════════════════════════════════════

@router.post("/{session_id}/units/{unit_id}/split")
async def split_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitSplitRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Split a unit into two. Auto-names and auto-updates echelon/SIDC.
    The child units drop one echelon level (e.g. company→platoon)."""
    from geoalchemy2.shape import from_shape, to_shape
    from shapely.geometry import Point as ShapelyPoint

    side = participant.side.value
    user_id = str(participant.user_id)

    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")

    if side not in ("admin", "observer"):
        if unit.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot split units from other side")
        has_authority = await check_command_authority(user_id, unit, session_id, db)
        if not has_authority:
            has_authority = await check_subordinate_authority(user_id, unit, session_id, db)
        if not has_authority:
            raise HTTPException(status_code=403, detail="No command authority over this unit")

    ratio = max(0.1, min(0.9, body.ratio))

    # ── Auto-naming ──
    base_name = unit.name
    # Strip existing /N suffix
    if "/" in base_name:
        prefix = base_name.rsplit("/", 1)[0]
    else:
        prefix = base_name

    # Find next available suffix numbers
    existing_names = set()
    siblings = await db.execute(
        select(Unit.name).where(
            Unit.session_id == session_id,
            Unit.is_destroyed == False,
            Unit.name.like(f"{prefix}/%"),
        )
    )
    for (n,) in siblings:
        existing_names.add(n)

    num = 1
    name_a = f"{prefix}/{num}"
    while name_a in existing_names:
        num += 1
        name_a = f"{prefix}/{num}"
    existing_names.add(name_a)
    num += 1
    name_b = body.new_name or f"{prefix}/{num}"

    # ── Echelon update: split units drop one echelon level ──
    current_echelon = get_current_echelon(unit.sidc)
    new_echelon = echelon_one_down(current_echelon)
    principal = get_principal_type(unit.unit_type)
    new_unit_type = make_unit_type(principal, new_echelon)
    new_sidc = update_sidc_echelon(unit.sidc, new_echelon)

    # ── Copy position with slight offset ──
    position_copy = None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            offset_lon = pt.x + 0.0004  # ~35m east offset
            position_copy = from_shape(ShapelyPoint(offset_lon, pt.y), srid=4326)
        except Exception:
            pass

    # ── Create new unit ──
    new_unit = Unit(
        session_id=session_id,
        side=unit.side,
        name=name_b,
        unit_type=new_unit_type,
        sidc=new_sidc,
        parent_unit_id=unit.parent_unit_id,
        position=position_copy,
        heading_deg=unit.heading_deg,
        strength=unit.strength * ratio,
        ammo=unit.ammo,
        morale=unit.morale,
        suppression=unit.suppression,
        comms_status=unit.comms_status,
        capabilities=dict(unit.capabilities) if unit.capabilities else None,
        move_speed_mps=unit.move_speed_mps,
        detection_range_m=unit.detection_range_m,
        assigned_user_ids=list(unit.assigned_user_ids) if unit.assigned_user_ids else None,
    )
    db.add(new_unit)

    # ── Update original ──
    unit.name = name_a
    unit.strength = unit.strength * (1 - ratio)
    unit.unit_type = new_unit_type
    unit.sidc = new_sidc

    await db.flush()
    await db.refresh(new_unit)

    return {
        "original": _serialize_unit(unit),
        "new_unit": _serialize_unit(new_unit),
    }


@router.post("/{session_id}/units/{unit_id}/merge")
async def merge_units(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    body: UnitMergeRequest,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Merge another unit into this one.
    Units must share the same principal type (e.g. infantry) and side,
    and be within 50 meters of each other.
    The survivor gains combined strength and moves up one echelon."""
    side = participant.side.value
    user_id = str(participant.user_id)

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

    # ── Validation ──
    surv_principal = get_principal_type(survivor.unit_type)
    abs_principal = get_principal_type(absorbed.unit_type)
    if surv_principal != abs_principal:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot merge different principal types ({surv_principal} vs {abs_principal})",
        )
    if survivor.side != absorbed.side:
        raise HTTPException(status_code=400, detail="Cannot merge units from different sides")
    if str(survivor.id) == str(absorbed.id):
        raise HTTPException(status_code=400, detail="Cannot merge a unit with itself")

    # ── Distance check (50m max) ──
    surv_lat, surv_lon = get_unit_latlon(survivor)
    abs_lat, abs_lon = get_unit_latlon(absorbed)
    if surv_lat is not None and abs_lat is not None:
        dist = haversine_m(surv_lat, surv_lon, abs_lat, abs_lon)
        if dist > 50:
            raise HTTPException(
                status_code=400,
                detail=f"Units must be within 50m to merge (current distance: {dist:.0f}m)",
            )

    # ── Authority check ──
    if side not in ("admin", "observer"):
        if survivor.side.value != side:
            raise HTTPException(status_code=403, detail="Cannot merge units from other side")
        auth1 = await check_command_authority(user_id, survivor, session_id, db)
        if not auth1:
            auth1 = await check_subordinate_authority(user_id, survivor, session_id, db)
        auth2 = await check_command_authority(user_id, absorbed, session_id, db)
        if not auth2:
            auth2 = await check_subordinate_authority(user_id, absorbed, session_id, db)
        if not auth1 or not auth2:
            raise HTTPException(status_code=403, detail="Need command authority over both units")

    # ── Weighted combine ──
    total_str = survivor.strength + absorbed.strength
    if total_str > 0:
        w_surv = survivor.strength / total_str
        w_abs = absorbed.strength / total_str
    else:
        w_surv = 0.5
        w_abs = 0.5

    survivor.strength = min(1.0, total_str)
    survivor.ammo = min(1.0, survivor.ammo * w_surv + absorbed.ammo * w_abs)
    survivor.morale = min(1.0, survivor.morale * w_surv + absorbed.morale * w_abs)
    survivor.suppression = max(0.0, survivor.suppression * w_surv + absorbed.suppression * w_abs)

    # ── Re-parent children ──
    child_result = await db.execute(select(Unit).where(Unit.parent_unit_id == absorbed.id))
    for child in child_result.scalars().all():
        child.parent_unit_id = survivor.id

    # ── Destroy absorbed unit ──
    absorbed.is_destroyed = True
    absorbed.strength = 0.0
    absorbed.current_task = None

    # ── Auto-name: strip /N suffixes ──
    name = survivor.name
    if "/" in name:
        name = name.rsplit("/", 1)[0]
    survivor.name = name

    # ── Echelon update: merged unit moves up one level ──
    current_echelon = get_current_echelon(survivor.sidc)
    new_echelon = echelon_one_up(current_echelon)
    principal = get_principal_type(survivor.unit_type)
    survivor.unit_type = make_unit_type(principal, new_echelon)
    survivor.sidc = update_sidc_echelon(survivor.sidc, new_echelon)

    await db.flush()

    return {
        "survivor": _serialize_unit(survivor),
        "absorbed_id": str(absorbed.id),
    }


@router.get("/{session_id}/units/{unit_id}/viewshed")
async def get_unit_viewshed(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
    rays: int = 72,
    step: float | None = None,
):
    """Return the LOS-based viewshed polygon for a unit as GeoJSON.
    
    The viewshed accounts for terrain elevation blocking and
    terrain feature occlusion (forest, urban, etc.).
    Falls back to a simple circle if no elevation data exists.
    """
    return await _compute_viewshed(session_id, unit_id, db, rays, step)


async def _compute_viewshed(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession,
    rays: int = 72,
    step: float | None = None,
):
    """Core viewshed computation, shared by participant and admin endpoints."""
    from backend.models.grid import GridDefinition
    from backend.models.terrain_cell import TerrainCell
    from backend.models.elevation_cell import ElevationCell
    from backend.engine.terrain import TerrainService, get_cached_terrain_data, set_cached_terrain_data
    from backend.services.grid_service import GridService
    from backend.services.los_service import LOSService

    # Load unit
    result = await db.execute(select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id))
    unit = result.scalar_one_or_none()
    if unit is None:
        raise HTTPException(status_code=404, detail="Unit not found")
    if unit.is_destroyed:
        raise HTTPException(status_code=404, detail="Unit is destroyed")
    if unit.position is None:
        raise HTTPException(status_code=400, detail="Unit has no position")

    # Fog-of-war note: any session participant may request viewshed for any
    # non-destroyed unit.  The real visibility filter lives on the unit-list
    # endpoint; if the client already has the unit's id it was already cleared
    # by fog-of-war (or by admin god-view).

    # Extract unit position
    from geoalchemy2.shape import to_shape
    pt = to_shape(unit.position)
    obs_lon, obs_lat = pt.x, pt.y
    det_range = unit.detection_range_m or 1500.0

    # Build TerrainService — use session-level cache to avoid DB re-reads
    sid_str = str(session_id)
    cached = get_cached_terrain_data(sid_str)
    terrain_cells_dict = None
    elevation_cells_dict = None
    grid_service = None

    if cached:
        terrain_cells_dict = cached["terrain_cells"]
        elevation_cells_dict = cached["elevation_cells"]
    else:
        tc_result = await db.execute(
            select(TerrainCell.snail_path, TerrainCell.terrain_type)
            .where(TerrainCell.session_id == session_id)
        )
        tc_rows = tc_result.all()
        if tc_rows:
            terrain_cells_dict = {row[0]: row[1] for row in tc_rows}

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
                elevation_cells_dict = {
                    row[0]: {"elevation_m": row[1], "slope_deg": row[2], "aspect_deg": row[3]}
                    for row in ec_rows
                }
        # Cache for future requests
        set_cached_terrain_data(sid_str, terrain_cells_dict, elevation_cells_dict)

    # Load grid service (lightweight, no cache needed)
    gd_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = gd_result.scalar_one_or_none()
    if gd:
        grid_service = GridService(gd)

    # Load scenario for terrain_meta fallback
    from backend.models.scenario import Scenario
    from backend.models.session import Session
    sess_result = await db.execute(select(Session).where(Session.id == session_id))
    session_obj = sess_result.scalar_one_or_none()
    terrain_meta = None
    if session_obj and session_obj.scenario_id:
        sc_result = await db.execute(select(Scenario).where(Scenario.id == session_obj.scenario_id))
        scenario = sc_result.scalar_one_or_none()
        if scenario:
            terrain_meta = scenario.terrain_meta

    terrain = TerrainService(
        terrain_meta=terrain_meta,
        terrain_cells=terrain_cells_dict,
        elevation_cells=elevation_cells_dict,
        grid_service=grid_service,
    )

    # Compute viewshed — use unit-type-specific eye height
    los = LOSService(terrain)
    # Clamp rays to reasonable range
    rays = max(24, min(360, rays))
    eye_h = UNIT_EYE_HEIGHTS.get(unit.unit_type, DEFAULT_EYE_HEIGHT)
    try:
        geojson = los.compute_viewshed_geojson(
            observer_lon=obs_lon,
            observer_lat=obs_lat,
            max_range_m=det_range,
            eye_height=eye_h,
            num_rays=rays,
            step_m=step,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Viewshed computation failed for unit %s: %s", unit_id, e)
        # Fallback: return a simple circle
        geojson = los.compute_viewshed_geojson(
            observer_lon=obs_lon,
            observer_lat=obs_lat,
            max_range_m=det_range,
            eye_height=eye_h,
            num_rays=rays,
            step_m=None,
        )
    geojson["properties"]["unit_id"] = str(unit_id)
    geojson["properties"]["unit_name"] = unit.name

    return geojson


@router.get("/{session_id}/pending-orders-count")
async def get_pending_orders_count(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """Return count of validated (pending execution) orders for the requester's side."""
    from backend.models.order import Order, OrderStatus
    from sqlalchemy import func

    side = participant.side.value
    if side in ("admin", "observer"):
        # Admin/observer sees all pending orders
        result = await db.execute(
            select(func.count(Order.id)).where(
                Order.session_id == session_id,
                Order.status.in_([OrderStatus.pending, OrderStatus.validated]),
            )
        )
    else:
        result = await db.execute(
            select(func.count(Order.id)).where(
                Order.session_id == session_id,
                Order.status.in_([OrderStatus.pending, OrderStatus.validated]),
                Order.issued_by_side == side,
            )
        )
    count = result.scalar() or 0
    return {"count": count}


@router.post("/{session_id}/units/{unit_id}/disband")
async def disband_unit(
    session_id: uuid.UUID,
    unit_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    """
    Disband (permanently remove) a unit. Re-parents children to the
    disbanded unit's parent. Generates a unit_disbanded event.
    """
    side = participant.side.value
    role = getattr(participant, 'role', None)
    if side == 'observer' or role == 'observer':
        raise HTTPException(status_code=403, detail="Observers cannot disband units")

    result = await db.execute(
        select(Unit).where(Unit.id == unit_id, Unit.session_id == session_id)
    )
    unit = result.scalar_one_or_none()
    if not unit:
        raise HTTPException(status_code=404, detail="Unit not found")
    if unit.is_destroyed:
        raise HTTPException(status_code=400, detail="Unit already destroyed")

    unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
    if side not in ('admin',) and unit_side != side:
        raise HTTPException(status_code=403, detail="Cannot disband units from other side")

    # Re-parent children to this unit's parent
    children_result = await db.execute(
        select(Unit).where(
            Unit.parent_unit_id == unit_id,
            Unit.session_id == session_id,
            Unit.is_destroyed == False,
        )
    )
    for child in children_result.scalars().all():
        child.parent_unit_id = unit.parent_unit_id

    # Mark unit as destroyed (disbanded)
    unit.is_destroyed = True
    unit.current_task = None
    unit.strength = 0.0

    # Generate event
    from backend.models.event import Event
    from datetime import datetime, timezone
    from backend.engine.events import create_event

    session_result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = session_result.scalar_one_or_none()
    tick = session.tick if session else 0
    game_time = session.current_time if session else datetime.now(timezone.utc)

    from backend.models.session import Session
    evt = create_event(session_id, tick, game_time, {
        "event_type": "unit_disbanded",
        "actor_unit_id": unit_id,
        "text_summary": f"{unit.name} has been disbanded by command",
        "payload": {"unit_id": str(unit_id), "unit_name": unit.name},
    }, "all")
    db.add(evt)

    await db.flush()

    # Broadcast via WebSocket
    from backend.services.ws_manager import ws_manager
    await ws_manager.broadcast(session_id, {
        "type": "event_new",
        "data": {
            "event_type": "unit_disbanded",
            "text_summary": f"{unit.name} has been disbanded by command",
        },
    })

    return {"ok": True, "unit_id": str(unit_id), "unit_name": unit.name}


@router.get("/{session_id}/contacts")
async def get_contacts(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    participant=Depends(get_session_participant),
):
    side = participant.side.value
    role = getattr(participant, 'role', None)
    # Observer and admin see all contacts
    if side in ("admin", "observer") or role == "observer":
        vis_side = "observer"
    elif side in ("blue", "red"):
        vis_side = side
    else:
        vis_side = "blue"
    return await get_visible_contacts(session_id, vis_side, db)
