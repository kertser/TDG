"""
Fog-of-war visibility service.

Filters world state → per-side visible view.
- Own-side units: always fully visible
- Opposing units: only visible if within detection range of own-side unit
  AND line-of-sight is not blocked by terrain
- Admin/observer: sees everything
"""

from __future__ import annotations

import uuid

from geoalchemy2.shape import to_shape
from sqlalchemy import select, func, cast
from sqlalchemy.ext.asyncio import AsyncSession
from geoalchemy2 import Geography

from sqlalchemy.orm import selectinload

from backend.models.unit import Unit
from backend.models.contact import Contact
from backend.models.session import SessionParticipant


# Unit types with trained concealment abilities (mirrors detection.py)
_CONCEALMENT_UNIT_TYPES = {
    "recon_team", "recon_section", "sniper_team", "observation_post",
    "engineer_recon_team",
}


def _is_concealed_unit(unit: Unit) -> bool:
    """Check if a unit is in concealment mode (mirrors detection.py logic).

    A unit is concealed when:
    - It is a concealment-capable type (recon, sniper, observation post)
    - It is NOT actively moving, attacking, or disengaging
    - Its morale is reasonable (above 0.25)
    """
    if unit.unit_type not in _CONCEALMENT_UNIT_TYPES:
        return False

    task = unit.current_task
    if task:
        task_type = task.get("type", "")
        if task_type in ("move", "advance", "attack", "engage", "fire", "disengage"):
            return False

    morale = unit.morale or 1.0
    if morale < 0.25:
        return False

    return True


def _serialize_unit(unit: Unit) -> dict:
    """Serialize a Unit ORM object to a dict with lat/lon extracted from PostGIS."""
    lat, lon = None, None
    if unit.position is not None:
        try:
            pt = to_shape(unit.position)
            lon, lat = pt.x, pt.y
        except Exception:
            pass

    # Compute unit status from current_task
    status = _compute_unit_status(unit)

    return {
        "id": str(unit.id),
        "session_id": str(unit.session_id),
        "side": unit.side.value,
        "name": unit.name,
        "unit_type": unit.unit_type,
        "sidc": unit.sidc,
        "parent_unit_id": str(unit.parent_unit_id) if unit.parent_unit_id else None,
        "lat": lat,
        "lon": lon,
        "heading_deg": unit.heading_deg,
        "strength": unit.strength,
        "ammo": unit.ammo,
        "morale": unit.morale,
        "suppression": unit.suppression,
        "comms_status": unit.comms_status.value if unit.comms_status else "operational",
        "current_task": unit.current_task,
        "capabilities": unit.capabilities,
        "move_speed_mps": unit.move_speed_mps,
        "detection_range_m": unit.detection_range_m,
        "is_destroyed": unit.is_destroyed,
        "assigned_user_ids": unit.assigned_user_ids,
        "unit_status": status,
        "formation": (unit.capabilities or {}).get("formation"),
    }


def _compute_unit_status(unit: Unit) -> str:
    """Derive a human-readable status string from unit state."""
    if unit.is_destroyed:
        return "destroyed"
    if unit.strength is not None and unit.strength <= 0:
        return "destroyed"
    if unit.morale is not None and unit.morale < 0.15:
        return "broken"
    if unit.suppression is not None and unit.suppression > 0.7:
        return "suppressed"

    task = unit.current_task
    if task:
        task_type = task.get("type", "")
        if task_type in ("attack", "engage", "fire"):
            return "engaging"
        if task.get("auto_return_fire"):
            return "engaging"
        if task_type in ("move", "advance"):
            return "moving"
        if task_type in ("retreat", "withdraw", "disengage"):
            return "retreating"
        if task_type in ("defend", "hold"):
            return "defending"
        if task_type in ("observe", "recon"):
            return "observing"
        if task_type in ("support",):
            return "supporting"
        if task_type:
            return task_type  # custom status

    return "idle"


def _make_enemy_label(unit_type: str) -> str:
    """Generate a generic label for enemy units (fog-of-war: hide real name).

    Maps unit_type to a vague category + size estimate, e.g. 'Infantry platoon'.
    """
    t = (unit_type or "").lower()
    category = "Unknown unit"
    if "infantry" in t or "mech" in t:
        category = "Infantry"
    elif "tank" in t:
        category = "Armored"
    elif "artillery" in t or "mortar" in t:
        category = "Artillery"
    elif "recon" in t or "sniper" in t or "observation" in t:
        category = "Recon"
    elif "engineer" in t or "mine" in t or "breacher" in t or "avlb" in t:
        category = "Engineer"
    elif "logistics" in t:
        category = "Support"
    elif "command" in t or "headquarters" in t:
        category = "Command"

    size = ""
    if "battalion" in t:
        size = " battalion"
    elif "company" in t or "battery" in t:
        size = " company"
    elif "platoon" in t:
        size = " platoon"
    elif "section" in t:
        size = " section"
    elif "team" in t or "squad" in t:
        size = " team"

    return category + size


def _generalize_unit_type(unit_type: str) -> str:
    """Generalize unit_type to a broad category for fog-of-war.
    
    Prevents exact personnel lookup — e.g., 'infantry_squad' → 'infantry',
    'tank_company' → 'armor', 'mortar_section' → 'artillery'.
    """
    t = (unit_type or "").lower()
    if "infantry" in t or "mech" in t:
        return "infantry"
    elif "tank" in t:
        return "armor"
    elif "artillery" in t or "mortar" in t:
        return "artillery"
    elif "recon" in t or "sniper" in t or "observation" in t:
        return "recon"
    elif "engineer" in t or "mine" in t or "breacher" in t or "avlb" in t:
        return "engineer"
    elif "logistics" in t:
        return "support"
    elif "command" in t or "headquarters" in t:
        return "command"
    return "unknown"


def _mask_sidc_echelon(sidc: str) -> str:
    """Mask the echelon field (positions 9-10, 0-indexed) in a MIL-STD-2525D SIDC.
    
    Sets echelon to '00' (unspecified) so the military symbol doesn't reveal
    exact unit size (team/squad/section/platoon/company/battalion).
    Also masks HQ indicator (position 8) to prevent deduction.
    """
    if not sidc or len(sidc) < 20:
        return sidc or ""
    # Positions are 1-based in the spec; 0-based in string indexing:
    # pos 8 (idx 7) = HQ/TF/FD, pos 9-10 (idx 8-9) = Echelon
    return sidc[:7] + "0" + "00" + sidc[10:]


def _serialize_contact(contact: Contact) -> dict:
    """Serialize a Contact ORM object to a dict with lat/lon."""
    lat, lon = None, None
    if contact.location_estimate is not None:
        try:
            pt = to_shape(contact.location_estimate)
            lon, lat = pt.x, pt.y
        except Exception:
            pass

    return {
        "id": str(contact.id),
        "session_id": str(contact.session_id),
        "observing_side": contact.observing_side.value,
        "estimated_type": contact.estimated_type,
        "estimated_size": contact.estimated_size,
        "lat": lat,
        "lon": lon,
        "location_accuracy_m": contact.location_accuracy_m,
        "confidence": contact.confidence,
        "last_seen_tick": contact.last_seen_tick,
        "source": contact.source,
        "is_stale": contact.is_stale,
    }


async def get_visible_units(
    session_id: uuid.UUID,
    side: str,
    db: AsyncSession,
) -> list[dict]:
    """
    Return units visible to the given side.

    - Own-side units: always visible (full detail)
    - Admin/observer: all units visible
    - Opposing units: only if there is an active (non-stale) contact for them
      from this side, OR if within guaranteed close visual range (200m) of
      any friendly unit. This prevents showing enemies that haven't actually
      been detected by the detection engine's probability roll.
    """
    if side in ("admin", "observer"):
        # See everything
        result = await db.execute(
            select(Unit).where(
                Unit.session_id == session_id,
                Unit.is_destroyed == False,
            )
        )
        return [_serialize_unit(u) for u in result.scalars().all()]

    # Normalize side to only allow blue/red for fog-of-war
    if side not in ("blue", "red"):
        # Unknown side — show nothing to prevent data leaks
        return []

    # Own-side units — always visible
    own_result = await db.execute(
        select(Unit).where(
            Unit.session_id == session_id,
            Unit.side == side,
            Unit.is_destroyed == False,
        )
    )
    own_units = own_result.scalars().all()

    # Opposing side
    opposing_side = "red" if side == "blue" else "blue"

    # ── Contact-based fog-of-war ──
    # 1. Find enemy unit IDs that have active (non-stale) contacts from our side
    contact_result = await db.execute(
        select(Contact.target_unit_id).where(
            Contact.session_id == session_id,
            Contact.observing_side == side,
            Contact.is_stale == False,
            Contact.target_unit_id.isnot(None),
        ).distinct()
    )
    contacted_unit_ids = {row[0] for row in contact_result.all()}

    # 2. Also find enemies within guaranteed close visual range (200m)
    #    At this distance, visual contact is virtually certain.
    GUARANTEED_VISUAL_RANGE_M = 200.0
    own_observer = (
        select(
            Unit.position.label("obs_pos"),
        )
        .where(
            Unit.session_id == session_id,
            Unit.side == side,
            Unit.is_destroyed == False,
            Unit.position.isnot(None),
        )
        .subquery()
    )

    close_enemy_query = (
        select(Unit.id)
        .where(
            Unit.session_id == session_id,
            Unit.side == opposing_side,
            Unit.is_destroyed == False,
            Unit.position.isnot(None),
        )
        .where(
            func.ST_DWithin(
                cast(Unit.position, Geography),
                cast(own_observer.c.obs_pos, Geography),
                GUARANTEED_VISUAL_RANGE_M,
            )
        )
    )

    try:
        close_result = await db.execute(close_enemy_query)
        close_unit_ids = {row[0] for row in close_result.all()}
    except Exception:
        close_unit_ids = set()

    # Combine: units with active contacts OR within close visual range
    visible_enemy_ids = contacted_unit_ids | close_unit_ids

    if not visible_enemy_ids:
        return [_serialize_unit(u) for u in own_units]

    # Fetch the actual enemy unit records
    enemy_result = await db.execute(
        select(Unit).where(
            Unit.session_id == session_id,
            Unit.side == opposing_side,
            Unit.is_destroyed == False,
            Unit.id.in_(visible_enemy_ids),
        )
    )
    enemy_units = enemy_result.scalars().all()

    # ── LOS filtering: verify each enemy unit has line-of-sight from
    # at least one friendly observer (terrain/elevation blocks visibility) ──
    if enemy_units:
        enemy_units = await _filter_by_los(
            session_id, side, own_units, enemy_units, db
        )

    # ── Pre-compute observer distances for identification detail level ──
    # Close-range identification: if a friendly unit is within IDENT_RANGE,
    # we can identify exact unit type, echelon, and personnel count.
    # Recon/sniper/OP units can identify from longer range.
    IDENT_RANGE_DEFAULT_M = 50.0    # any unit can identify at 50m
    IDENT_RANGE_RECON_M = 500.0     # recon/sniper/OP can identify from 500m

    _RECON_IDENT_TYPES = {
        "recon_team", "recon_section", "sniper_team",
        "observation_post", "engineer_recon_team",
    }

    # Build own-unit position list with unit_type for recon check
    _own_positions_for_ident: list[tuple[float, float, str]] = []
    for _ou in own_units:
        if _ou.is_destroyed or _ou.position is None:
            continue
        try:
            _oup = to_shape(_ou.position)
            _own_positions_for_ident.append((_oup.y, _oup.x, _ou.unit_type or ""))
        except Exception:
            continue

    def _can_identify_enemy(enemy_unit: Unit) -> bool:
        """Check if any friendly observer is close enough to identify full detail."""
        if enemy_unit.position is None:
            return False
        try:
            ept = to_shape(enemy_unit.position)
            e_lat, e_lon = ept.y, ept.x
        except Exception:
            return False
        for o_lat, o_lon, o_type in _own_positions_for_ident:
            dlat = (e_lat - o_lat) * 111320.0
            dlon = (e_lon - o_lon) * 74000.0
            dist = (dlat * dlat + dlon * dlon) ** 0.5
            # Any unit at very close range can visually identify
            if dist <= IDENT_RANGE_DEFAULT_M:
                return True
            # Recon/sniper/OP units can identify from longer range
            if o_type in _RECON_IDENT_TYPES and dist <= IDENT_RANGE_RECON_M:
                return True
        return False

    all_visible = [_serialize_unit(u) for u in own_units]
    for u in enemy_units:
        serialized = _serialize_unit(u)
        # Reduce detail for detected enemy units (fog-of-war: partial info)
        serialized["ammo"] = None
        serialized["morale"] = None
        serialized["suppression"] = None
        serialized["comms_status"] = None
        serialized["current_task"] = None
        serialized["move_speed_mps"] = None
        serialized["detection_range_m"] = None
        serialized["assigned_user_ids"] = None
        serialized["capabilities"] = None
        serialized["formation"] = None

        # ── Distance-based identification ──
        # At close range (or recon observation), we can identify exact unit
        # type, echelon, name, and accurate strength.
        identified = _can_identify_enemy(u)

        if identified:
            # Close/recon identification: show real type, SIDC, and name
            serialized["real_name"] = serialized["name"]
            serialized["real_unit_type"] = serialized["unit_type"]
            serialized["real_sidc"] = serialized["sidc"]
            # Keep real unit_type, sidc, and name — don't generalize
            # Still quantize strength to 10% buckets (closer = more accurate)
            raw_strength = serialized.get("strength") or 1.0
            if raw_strength > 0.90:
                serialized["strength"] = round(raw_strength, 1)
                serialized["strength_estimate"] = "full"
            elif raw_strength > 0.50:
                serialized["strength"] = round(raw_strength * 10) / 10  # 10% buckets
                serialized["strength_estimate"] = "reduced"
            elif raw_strength > 0.25:
                serialized["strength"] = round(raw_strength * 10) / 10
                serialized["strength_estimate"] = "weakened"
            else:
                serialized["strength"] = round(raw_strength * 10) / 10
                serialized["strength_estimate"] = "critical"
            serialized["identified"] = True
        else:
            # Standard fog-of-war: generalize type, mask SIDC, hide name
            serialized["real_name"] = serialized["name"]
            serialized["name"] = _make_enemy_label(u.unit_type)
            serialized["real_unit_type"] = serialized["unit_type"]
            serialized["unit_type"] = _generalize_unit_type(u.unit_type)
            serialized["real_sidc"] = serialized["sidc"]
            serialized["sidc"] = _mask_sidc_echelon(u.sidc)
            # Quantize strength to 25% buckets (approximate observation)
            raw_strength = serialized.get("strength") or 1.0
            if raw_strength > 0.75:
                serialized["strength"] = 1.0
                serialized["strength_estimate"] = "full"
            elif raw_strength > 0.50:
                serialized["strength"] = 0.75
                serialized["strength_estimate"] = "reduced"
            elif raw_strength > 0.25:
                serialized["strength"] = 0.50
                serialized["strength_estimate"] = "weakened"
            else:
                serialized["strength"] = 0.25
                serialized["strength_estimate"] = "critical"
            serialized["identified"] = False
        serialized["is_enemy"] = True
        all_visible.append(serialized)

    return all_visible


async def _filter_by_los(
    session_id: uuid.UUID,
    side: str,
    own_units: list,
    enemy_units: list,
    db: AsyncSession,
) -> list:
    """Filter enemy units through line-of-sight checks.

    For each candidate enemy unit (already within ST_DWithin range),
    check that at least one friendly unit has unblocked LOS to it.
    Uses cached terrain/elevation data for speed.
    """
    from backend.models.terrain_cell import TerrainCell
    from backend.models.elevation_cell import ElevationCell
    from backend.models.grid import GridDefinition
    from backend.engine.terrain import (
        TerrainService, get_cached_terrain_data, set_cached_terrain_data,
    )
    from backend.services.los_service import LOSService

    # Build TerrainService from cache or DB
    sid_str = str(session_id)
    cached = get_cached_terrain_data(sid_str)
    terrain_cells_dict = None
    elevation_cells_dict = None

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
        set_cached_terrain_data(sid_str, terrain_cells_dict, elevation_cells_dict)

    # If no elevation data AND no terrain cells, skip LOS filtering (all pass)
    if not elevation_cells_dict and not terrain_cells_dict:
        return enemy_units

    # Load grid service
    grid_service = None
    gd_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = gd_result.scalar_one_or_none()
    if gd:
        from backend.services.grid_service import GridService
        grid_service = GridService(gd)

    if not grid_service:
        return enemy_units

    terrain = TerrainService(
        terrain_cells=terrain_cells_dict,
        elevation_cells=elevation_cells_dict,
        grid_service=grid_service,
    )
    los = LOSService(terrain)

    # Unit-type-specific eye heights (must match detection.py / units.py)
    UNIT_EYE_HEIGHTS = {
        "observation_post": 8.0, "tank_company": 3.0, "tank_platoon": 3.0,
        "mech_company": 2.8, "mech_platoon": 2.8, "recon_team": 3.0,
        "recon_section": 3.0, "sniper_team": 2.5, "headquarters": 3.0,
        "command_post": 3.0, "artillery_battery": 2.5, "artillery_platoon": 2.5,
    }
    DEFAULT_EYE_H = 2.0

    # Pre-extract own unit positions
    own_positions = []
    for u in own_units:
        if u.is_destroyed or u.position is None:
            continue
        try:
            pt = to_shape(u.position)
            eye_h = UNIT_EYE_HEIGHTS.get(u.unit_type, DEFAULT_EYE_H)
            own_positions.append((pt.x, pt.y, u.detection_range_m or 1500.0, eye_h))
        except Exception:
            continue

    if not own_positions:
        return enemy_units

    # Filter: enemy must have LOS from at least one own unit
    visible = []
    for enemy in enemy_units:
        if enemy.position is None:
            continue
        try:
            ept = to_shape(enemy.position)
            e_lon, e_lat = ept.x, ept.y
        except Exception:
            continue

        # ── Concealment check: concealed recon/sniper/OP units use severely
        # reduced detection range, matching the tick engine's detection.py logic.
        enemy_concealed = _is_concealed_unit(enemy)

        has_any_los = False
        for obs_lon, obs_lat, det_range, eye_h in own_positions:

            if enemy_concealed:
                # Concealed units: max 300m range, further reduced by terrain at
                # target position. Matches CONCEALMENT_MAX_RANGE_M in detection.py.
                target_terrain_vis = terrain.visibility_factor(e_lon, e_lat)
                effective_range = 300.0 * target_terrain_vis  # forest=120m, open=300m
            else:
                # Use base detection range WITHOUT terrain visibility reduction.
                # Terrain visibility affects detection PROBABILITY in the tick engine,
                # not the visibility range. This keeps fog-of-war range consistent
                # with the detection engine's range check.
                effective_range = det_range

                # Apply height advantage bonus to effective range
                height_bonus = terrain.detection_height_bonus(
                    obs_lon, obs_lat, e_lon, e_lat
                )
                effective_range *= height_bonus

            dlat = (e_lat - obs_lat) * 111320.0
            dlon = (e_lon - obs_lon) * 74000.0
            dist = (dlat * dlat + dlon * dlon) ** 0.5
            if dist > effective_range:
                continue
            if los.has_los(obs_lon, obs_lat, e_lon, e_lat, eye_height=eye_h):
                has_any_los = True
                break

        if has_any_los:
            visible.append(enemy)

    return visible


async def get_visible_contacts(
    session_id: uuid.UUID,
    side: str,
    db: AsyncSession,
) -> list[dict]:
    """Return contacts visible to the given side."""
    if side in ("admin", "observer"):
        result = await db.execute(
            select(Contact).where(Contact.session_id == session_id)
        )
    else:
        result = await db.execute(
            select(Contact).where(
                Contact.session_id == session_id,
                Contact.observing_side == side,
            )
        )
    return [_serialize_contact(c) for c in result.scalars().all()]


async def _get_user_map(session_id: uuid.UUID, db: AsyncSession) -> dict[str, str]:
    """Return a mapping of user_id (str) → display_name for session participants."""
    result = await db.execute(
        select(SessionParticipant)
        .options(selectinload(SessionParticipant.user))
        .where(SessionParticipant.session_id == session_id)
    )
    participants = result.scalars().all()
    return {
        str(p.user_id): p.user.display_name
        for p in participants
        if p.user
    }


async def enrich_units_with_command_info(
    units_data: list[dict],
    session_id: uuid.UUID,
    db: AsyncSession,
    requesting_side: str | None = None,
) -> list[dict]:
    """
    Add commanding_user_name and assigned_user_names to each unit dict.

    For each unit, the commanding user is determined by walking UP the parent
    chain (including self) and finding the first unit with an assigned user.
    This mirrors real military Chain of Command.
    """
    user_map = await _get_user_map(session_id, db)

    # Build unit lookup by ID for parent-chain walking
    unit_lookup = {u["id"]: u for u in units_data}

    for u in units_data:
        # For enemy units behind fog-of-war, don't expose command structure
        if (
            requesting_side
            and requesting_side not in ("admin", "observer")
            and u.get("side") != requesting_side
        ):
            u["assigned_user_names"] = []
            u["commanding_user_name"] = None
            continue

        # Resolve assigned user IDs → display names
        names = []
        if u.get("assigned_user_ids"):
            for uid in u["assigned_user_ids"]:
                name = user_map.get(uid)
                if name:
                    names.append(name)
        u["assigned_user_names"] = names

        # Walk up parent chain to find commanding officer ABOVE this unit.
        # CO is the first assigned user found starting from the PARENT unit
        # (not the unit itself). This mirrors real CoC: CO is your superior.
        cmd_name = None
        parent_id = u.get("parent_unit_id")
        visited = set()
        while parent_id and parent_id in unit_lookup:
            if parent_id in visited:
                break
            visited.add(parent_id)
            parent = unit_lookup[parent_id]

            if parent.get("assigned_user_ids"):
                for uid in parent["assigned_user_ids"]:
                    name = user_map.get(uid)
                    if name:
                        cmd_name = name
                        break
                if cmd_name:
                    break

            parent_id = parent.get("parent_unit_id")
        u["commanding_user_name"] = cmd_name

    return units_data


async def check_command_authority(
    user_id: str,
    unit: Unit,
    session_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """
    Check if a user has command authority over a unit via the hierarchy.

    A user has authority if they are assigned to the unit itself
    OR to any proper ancestor in the parent chain.

    Special case: if NO units *on the same side* have any assigned_user_ids,
    then any player on the correct side has implicit authority (no CoC setup).
    """
    # Check self first
    if unit.assigned_user_ids and user_id in unit.assigned_user_ids:
        return True

    # Load lightweight unit data for parent-chain walk
    result = await db.execute(
        select(Unit.id, Unit.parent_unit_id, Unit.assigned_user_ids, Unit.side).where(
            Unit.session_id == session_id, Unit.is_destroyed == False
        )
    )
    rows = result.all()

    # If no units *on the same side* have any assigned_user_ids,
    # grant implicit authority to any player (no CoC configured for this side yet)
    unit_side = unit.side.value if hasattr(unit.side, 'value') else unit.side
    any_assigned = any(
        r[2] for r in rows
        if (r[3].value if hasattr(r[3], 'value') else r[3]) == unit_side and r[2]
    )
    if not any_assigned:
        return True

    unit_info = {
        str(r[0]): (str(r[1]) if r[1] else None, r[2])
        for r in rows
    }

    # Walk up parent chain
    current_parent_id = str(unit.parent_unit_id) if unit.parent_unit_id else None
    visited = set()
    while current_parent_id and current_parent_id not in visited:
        visited.add(current_parent_id)
        info = unit_info.get(current_parent_id)
        if info is None:
            break
        parent_parent_id, assigned_ids = info
        if assigned_ids and user_id in assigned_ids:
            return True
        current_parent_id = parent_parent_id

    return False


async def check_subordinate_authority(
    user_id: str,
    unit: Unit,
    session_id: uuid.UUID,
    db: AsyncSession,
) -> bool:
    """
    Check if a user has authority over a unit via subordinate user relationship.

    User A has authority over unit U if:
    - U is assigned to user B
    - And B is subordinate to A (i.e., B is assigned to a unit that is a descendant
      of a unit assigned to A in the unit hierarchy).
    """
    # If the unit has no assigned users, this check doesn't apply
    unit_assigned = unit.assigned_user_ids
    if not unit_assigned:
        return False

    # Load all units with their parent chain and assignments
    result = await db.execute(
        select(Unit.id, Unit.parent_unit_id, Unit.assigned_user_ids).where(
            Unit.session_id == session_id, Unit.is_destroyed == False
        )
    )
    rows = result.all()
    unit_info = {
        str(r[0]): (str(r[1]) if r[1] else None, r[2])
        for r in rows
    }

    # For each user assigned to the target unit, check if they are subordinate to user_id
    for assigned_uid in unit_assigned:
        if assigned_uid == user_id:
            continue  # Already checked in check_command_authority
        # Find all units assigned to this subordinate user
        for uid_str, (parent_id, assigned_ids) in unit_info.items():
            if not assigned_ids or assigned_uid not in assigned_ids:
                continue
            # Walk up this unit's parent chain looking for a unit assigned to user_id
            current_parent = parent_id
            visited = set()
            while current_parent and current_parent not in visited:
                visited.add(current_parent)
                info = unit_info.get(current_parent)
                if info is None:
                    break
                pp_id, pp_assigned = info
                if pp_assigned and user_id in pp_assigned:
                    return True  # user_id commands an ancestor → subordinate relationship
                current_parent = pp_id

    return False


