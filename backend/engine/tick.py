"""
Main tick orchestrator – runs one simulation tick for a session.

AGENTS.MD Section 8.1 tick sequence:
  1. Process pending validated orders → assign tasks to units
  2. Execute movement for all units with movement tasks
  3. Execute detection checks (Blue→Red and Red→Blue)
  4. Decay stale contacts
  5. Execute combat resolution for engaged units
  6. Apply suppression recovery
  7. Apply morale effects
  8. Update communication status
  9. Consume ammo
  10. Generate events and reports
  11. Advance session tick counter and game_time
  12. Broadcast state_update to all connected clients
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.session import Session, SessionStatus
from backend.models.scenario import Scenario
from backend.models.unit import Unit
from backend.models.order import Order, OrderStatus
from backend.models.contact import Contact
from backend.models.event import Event
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell
from backend.models.grid import GridDefinition
from backend.models.map_object import MapObject

from backend.engine.terrain import TerrainService
from backend.engine.movement import process_movement
from backend.engine.detection import process_detection
from backend.engine.combat import process_combat
from backend.engine.morale import process_morale
from backend.engine.comms import process_comms
from backend.engine.contacts import process_contacts
from backend.engine.ammo import process_ammo
from backend.engine.suppression import process_suppression_recovery
from backend.engine.events import create_event
from backend.engine.engineering import process_engineering
from backend.engine.structures import process_structures


async def run_tick(session_id: uuid.UUID, db: AsyncSession) -> dict:
    """
    Execute one simulation tick for the given session.

    This is the main entry point for the rules engine.
    All mutations are deterministic — no LLM involvement.

    Returns:
        dict with tick results {tick, events_count, ...}
    """
    # Load session with scenario
    result = await db.execute(
        select(Session).where(Session.id == session_id)
    )
    session = result.scalar_one_or_none()
    if session is None:
        raise ValueError(f"Session {session_id} not found")
    if session.status != SessionStatus.running:
        raise ValueError(f"Session not running (status={session.status.value})")

    # Load scenario for terrain data
    result = await db.execute(
        select(Scenario).where(Scenario.id == session.scenario_id)
    )
    scenario = result.scalar_one_or_none()

    # ── Build TerrainService: prefer DB cells, fallback to terrain_meta ──
    terrain_cells_dict = None
    elevation_cells_dict = None
    grid_service = None

    # Try to load terrain cells from DB
    tc_result = await db.execute(
        select(TerrainCell.snail_path, TerrainCell.terrain_type)
        .where(TerrainCell.session_id == session_id)
    )
    tc_rows = tc_result.all()
    if tc_rows:
        terrain_cells_dict = {row[0]: row[1] for row in tc_rows}

        # Load elevation cells
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

        # Load grid service for point→snail resolution
        gd_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        gd = gd_result.scalar_one_or_none()
        if gd:
            from backend.services.grid_service import GridService
            grid_service = GridService(gd)

    terrain = TerrainService(
        terrain_meta=scenario.terrain_meta if scenario else None,
        terrain_cells=terrain_cells_dict,
        elevation_cells=elevation_cells_dict,
        grid_service=grid_service,
    )

    # Build LOSService for line-of-sight checks in detection
    from backend.services.los_service import LOSService
    los_service = LOSService(terrain)

    tick = session.tick
    tick_duration = session.tick_interval or 60  # seconds
    game_time = session.current_time or datetime.now(timezone.utc)

    all_events: list[dict] = []

    # ── 1. Process pending orders → assign tasks ─────────────────
    order_events = await _process_orders(session_id, tick, db)
    all_events.extend(order_events)

    # ── Load all units for this session ──────────────────────────
    result = await db.execute(
        select(Unit).where(Unit.session_id == session_id)
    )
    all_units = list(result.scalars().all())

    blue_units = [u for u in all_units if not u.is_destroyed and u.side.value == "blue"]
    red_units = [u for u in all_units if not u.is_destroyed and u.side.value == "red"]

    # ── Load map objects (obstacles, structures) ─────────────
    mo_result = await db.execute(
        select(MapObject).where(MapObject.session_id == session_id)
    )
    map_objects_list = list(mo_result.scalars().all())

    # ── 2. Execute movement (with obstacle effects) ──────────
    movement_events = process_movement(all_units, tick_duration, terrain, map_objects_list)
    all_events.extend(movement_events)

    # ── 2b. Process engineering tasks ─────────────────────────
    new_map_objects: list = []
    eng_events = process_engineering(all_units, map_objects_list, session_id, new_map_objects)
    all_events.extend(eng_events)
    for new_obj in new_map_objects:
        db.add(new_obj)
        map_objects_list.append(new_obj)

    # ── 3. Execute detection ─────────────────────────────────────
    # Reload unit lists after movement (positions changed)
    blue_units = [u for u in all_units if not u.is_destroyed and u.side.value == "blue"]
    red_units = [u for u in all_units if not u.is_destroyed and u.side.value == "red"]

    weather_mod = 1.0
    if scenario and scenario.environment:
        vis_km = scenario.environment.get("visibility_km", 5.0)
        weather_mod = min(1.0, vis_km / 5.0)

    new_contacts_data = process_detection(
        blue_units, red_units, tick, terrain, weather_mod,
        los_service=los_service,
    )

    # Upsert contacts
    contact_events = await _upsert_contacts(session_id, tick, game_time, new_contacts_data, db)
    all_events.extend(contact_events)

    # ── 3b. Map object discovery (LOS-based) ──────────────────
    discovery_events = _process_object_discovery(
        blue_units, red_units, map_objects_list, terrain, los_service,
    )
    all_events.extend(discovery_events)

    # ── 4. Decay stale contacts ──────────────────────────────────
    result = await db.execute(
        select(Contact).where(Contact.session_id == session_id)
    )
    existing_contacts = list(result.scalars().all())
    contacts_to_delete, stale_events = process_contacts(existing_contacts, tick)
    all_events.extend(stale_events)
    for c in contacts_to_delete:
        await db.delete(c)

    # ── 5. Execute combat ────────────────────────────────────────
    combat_events, under_fire = process_combat(all_units, terrain, map_objects_list)
    all_events.extend(combat_events)

    # ── 6. Suppression recovery ──────────────────────────────
    process_suppression_recovery(all_units, under_fire)

    # ── 7. Morale effects ────────────────────────────────────
    morale_events = process_morale(all_units, under_fire)
    all_events.extend(morale_events)

    # ── 8. Communications ────────────────────────────────────
    comms_events = process_comms(all_units, under_fire)
    all_events.extend(comms_events)

    # ── 9. Ammo consumption ──────────────────────────────────
    ammo_events = process_ammo(all_units, under_fire)
    all_events.extend(ammo_events)

    # ── 9b. Structure effects (resupply, comms bonus) ─────────
    struct_events = process_structures(all_units, map_objects_list)
    all_events.extend(struct_events)

    # ── 10. Persist events ───────────────────────────────────────
    for evt_dict in all_events:
        # Determine visibility based on event type and involved units
        vis = _determine_visibility(evt_dict)
        event_row = create_event(session_id, tick, game_time, evt_dict, vis)
        db.add(event_row)

    # ── 11. Advance tick ─────────────────────────────────────────
    session.tick = tick + 1
    session.current_time = game_time + timedelta(seconds=tick_duration)

    await db.flush()

    return {
        "tick": session.tick,
        "game_time": session.current_time.isoformat() if session.current_time else None,
        "events_count": len(all_events),
        "units_alive": sum(1 for u in all_units if not u.is_destroyed),
    }


async def _process_orders(
    session_id: uuid.UUID,
    tick: int,
    db: AsyncSession,
) -> list[dict]:
    """
    Process pending/validated orders and assign tasks to units.
    """
    events = []

    result = await db.execute(
        select(Order).where(
            Order.session_id == session_id,
            Order.status.in_([OrderStatus.pending, OrderStatus.validated]),
        )
    )
    orders = list(result.scalars().all())

    for order in orders:
        # For MVP: auto-validate pending orders
        if order.status == OrderStatus.pending:
            order.status = OrderStatus.validated

        # Extract task from parsed_order or parsed_intent
        task = _order_to_task(order)
        if task is None:
            order.status = OrderStatus.failed
            events.append({
                "event_type": "order_completed",
                "text_summary": f"Order failed: could not parse task",
                "payload": {"order_id": str(order.id), "reason": "no_task"},
            })
            continue

        # Assign task to target units
        if order.target_unit_ids:
            for unit_id in order.target_unit_ids:
                result = await db.execute(
                    select(Unit).where(
                        Unit.id == unit_id,
                        Unit.session_id == session_id,
                        Unit.is_destroyed == False,
                    )
                )
                unit = result.scalar_one_or_none()
                if unit:
                    unit.current_task = task
                    events.append({
                        "event_type": "order_issued",
                        "actor_unit_id": unit_id,
                        "text_summary": f"{unit.name} received order: {task.get('type', 'unknown')}",
                        "payload": {"order_id": str(order.id), "task": task},
                    })

        order.status = OrderStatus.executing

    return events


def _order_to_task(order: Order) -> dict | None:
    """
    Convert an Order into a unit task dict.

    Tries parsed_intent → parsed_order → fallback from original_text keywords.
    """
    # Try parsed intent first
    if order.parsed_intent:
        intent = order.parsed_intent
        task_type = intent.get("action", intent.get("type"))
        target_loc = intent.get("target_location", intent.get("destination"))
        if task_type and target_loc:
            return {
                "type": task_type,
                "target_location": target_loc,
                "order_id": str(order.id),
            }

    # Try parsed order
    if order.parsed_order:
        po = order.parsed_order
        task_type = po.get("order_type", po.get("type", order.order_type))
        target_loc = po.get("target_location", po.get("destination"))
        target_unit = po.get("target_unit_id")
        task = {"type": task_type, "order_id": str(order.id)}
        if target_loc:
            task["target_location"] = target_loc
        if target_unit:
            task["target_unit_id"] = target_unit
        return task

    # Fallback: try to parse simple keywords from original text
    if order.original_text:
        text = order.original_text.lower()
        if "move" in text or "advance" in text:
            return {"type": "move", "order_id": str(order.id)}
        if "attack" in text or "engage" in text:
            return {"type": "attack", "order_id": str(order.id)}
        if "defend" in text or "hold" in text:
            return {"type": "defend", "order_id": str(order.id)}
        if "observe" in text or "recon" in text:
            return {"type": "observe", "order_id": str(order.id)}

    return None


async def _upsert_contacts(
    session_id: uuid.UUID,
    tick: int,
    game_time: datetime,
    new_contacts_data: list[dict],
    db: AsyncSession,
) -> list[dict]:
    """
    Create or update contacts from detection results.
    """
    events = []

    for cd in new_contacts_data:
        # Check if contact for this specific target already exists
        target_uid = cd.get("target_unit_id")
        result = await db.execute(
            select(Contact).where(
                Contact.session_id == session_id,
                Contact.observing_side == cd["observing_side"],
            )
        )
        existing = result.scalars().all()

        # Find existing contact for the SAME target unit
        updated = False
        for contact in existing:
            # Match by target_unit_id for precise contact tracking
            # (Previously matched only by source, causing different targets
            # to incorrectly merge into one contact)
            contact_target = None
            if hasattr(contact, 'target_unit_id'):
                contact_target = contact.target_unit_id
            if target_uid and contact_target and str(contact_target) == str(target_uid):
                # Update existing contact for this target
                contact.location_estimate = from_shape(
                    Point(cd["lon"], cd["lat"]), srid=4326
                )
                contact.location_accuracy_m = cd["location_accuracy_m"]
                contact.confidence = cd["confidence"]
                contact.last_seen_tick = tick
                contact.last_seen_at = game_time
                contact.estimated_type = cd["estimated_type"]
                contact.is_stale = False
                contact.source = cd.get("source", "visual")
                updated = True
                break

        if not updated:
            # Create new contact
            contact = Contact(
                session_id=session_id,
                observing_side=cd["observing_side"],
                observing_unit_id=cd.get("observing_unit_id"),
                target_unit_id=cd.get("target_unit_id"),
                estimated_type=cd.get("estimated_type"),
                estimated_size=cd.get("estimated_size"),
                location_estimate=from_shape(
                    Point(cd["lon"], cd["lat"]), srid=4326
                ),
                location_accuracy_m=cd["location_accuracy_m"],
                confidence=cd["confidence"],
                last_seen_tick=tick,
                last_seen_at=game_time,
                source=cd.get("source", "visual"),
            )
            db.add(contact)

            events.append({
                "event_type": "contact_new",
                "text_summary": f"New contact: {cd.get('estimated_type', 'unknown')} detected",
                "payload": {
                    "observing_side": cd["observing_side"],
                    "estimated_type": cd.get("estimated_type"),
                    "lat": cd["lat"],
                    "lon": cd["lon"],
                    "confidence": cd["confidence"],
                },
            })

    return events


def _determine_visibility(event_dict: dict) -> str:
    """Determine which sides can see this event."""
    etype = event_dict.get("event_type", "")
    # Combat events visible to all (both sides see combat)
    if etype in ("combat", "unit_destroyed"):
        return "all"
    # Contact events only visible to the detecting side
    if etype in ("contact_new", "contact_lost"):
        side = event_dict.get("payload", {}).get("observing_side")
        return side if side else "all"
    # Object discovery visible to the discovering side
    if etype == "object_discovered":
        side = event_dict.get("payload", {}).get("side")
        return side if side else "all"
    # Order events visible to the issuing side
    if etype in ("order_issued", "order_completed"):
        return "all"  # MVP: visible to all
    return "all"


def _process_object_discovery(
    blue_units: list,
    red_units: list,
    map_objects: list,
    terrain: TerrainService,
    los_service,
) -> list[dict]:
    """
    Check if any units have LOS to undiscovered map objects.
    When a unit can see an object, mark it discovered for that side.
    This is a one-way flip: once discovered, stays discovered.
    """
    import math
    from backend.engine.detection import UNIT_EYE_HEIGHTS, DEFAULT_EYE_HEIGHT

    events = []
    if not map_objects:
        return events

    METERS_PER_DEG_LAT = 111_320.0
    METERS_PER_DEG_LON_AT_48 = 74_000.0

    # Build list of objects needing discovery check per side
    blue_undiscovered = [o for o in map_objects if not o.discovered_by_blue and o.is_active]
    red_undiscovered = [o for o in map_objects if not o.discovered_by_red and o.is_active]

    if not blue_undiscovered and not red_undiscovered:
        return events

    def _get_object_position(obj):
        """Extract centroid lat/lon from a map object's geometry."""
        if obj.geometry is None:
            return None
        try:
            shape = to_shape(obj.geometry)
            centroid = shape.centroid
            return centroid.y, centroid.x  # lat, lon
        except Exception:
            return None

    def _check_discovery(units, undiscovered_objects, side_attr):
        """Check if any unit from a side can see undiscovered objects."""
        side_events = []
        # Pre-extract unit positions
        unit_positions = []
        for u in units:
            if u.is_destroyed or u.position is None:
                continue
            try:
                pt = to_shape(u.position)
                eye_h = UNIT_EYE_HEIGHTS.get(u.unit_type, DEFAULT_EYE_HEIGHT)
                det_range = u.detection_range_m or 1500.0
                unit_positions.append((pt.x, pt.y, det_range, eye_h))
            except Exception:
                continue

        if not unit_positions:
            return side_events

        for obj in undiscovered_objects:
            pos = _get_object_position(obj)
            if pos is None:
                continue
            obj_lat, obj_lon = pos

            for obs_lon, obs_lat, det_range, eye_h in unit_positions:
                # Distance check
                dlat = (obj_lat - obs_lat) * METERS_PER_DEG_LAT
                dlon = (obj_lon - obs_lon) * METERS_PER_DEG_LON_AT_48
                dist = math.sqrt(dlat * dlat + dlon * dlon)
                if dist > det_range:
                    continue

                # LOS check
                if los_service is not None:
                    if not los_service.has_los(obs_lon, obs_lat, obj_lon, obj_lat,
                                               eye_height=eye_h):
                        continue

                # Object discovered!
                setattr(obj, side_attr, True)
                side_name = "blue" if side_attr == "discovered_by_blue" else "red"
                label = obj.label or obj.object_type.replace('_', ' ')
                side_events.append({
                    "event_type": "object_discovered",
                    "text_summary": f"{side_name.title()} forces discovered: {label}",
                    "payload": {
                        "side": side_name,
                        "object_id": str(obj.id),
                        "object_type": obj.object_type,
                    },
                })
                break  # One unit seeing it is enough

        return side_events

    events.extend(_check_discovery(blue_units, blue_undiscovered, "discovered_by_blue"))
    events.extend(_check_discovery(red_units, red_undiscovered, "discovered_by_red"))

    return events


