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

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.session import Session, SessionStatus
from backend.models.scenario import Scenario


def _iso_utc(dt: datetime | None) -> str | None:
    """Return ISO string with Z suffix for naive datetimes (assumed UTC)."""
    if dt is None:
        return None
    s = dt.isoformat()
    if not s.endswith('Z') and '+' not in s:
        s += 'Z'
    return s
from backend.models.unit import Unit
from backend.models.order import Order, OrderStatus
from backend.models.contact import Contact
from backend.models.event import Event
from backend.models.report import Report, ReportSide
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell
from backend.models.grid import GridDefinition
from backend.models.map_object import MapObject

from backend.engine.terrain import TerrainService
from backend.engine.movement import process_movement
from backend.engine.detection import process_detection
from backend.engine.combat import process_combat, process_artillery_support
from backend.engine.morale import process_morale
from backend.engine.comms import process_comms
from backend.engine.contacts import process_contacts
from backend.engine.ammo import process_ammo
from backend.engine.suppression import process_suppression_recovery
from backend.engine.events import create_event
from backend.engine.engineering import process_engineering
from backend.engine.structures import process_structures
from backend.engine.resupply import process_resupply

logger = logging.getLogger(__name__)


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
    if session.status == SessionStatus.finished:
        raise ValueError("Session is finished — reset it first")
    # Auto-resume paused/lobby sessions so the tick button always works
    if session.status in (SessionStatus.paused, SessionStatus.lobby):
        session.status = SessionStatus.running
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

    # Try session-level cache first (avoids DB query every tick)
    from backend.engine.terrain import get_cached_terrain_data, set_cached_terrain_data
    _sid_str = str(session_id)
    _cached = get_cached_terrain_data(_sid_str)
    if _cached:
        terrain_cells_dict = _cached.get("terrain_cells")
        elevation_cells_dict = _cached.get("elevation_cells")
    else:
        # Load terrain cells from DB
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

        # Cache for future ticks
        set_cached_terrain_data(_sid_str, terrain_cells_dict, elevation_cells_dict)

    # Always build grid_service (needed for radio chatter grid refs on every tick)
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
    _tick_start = time.monotonic()

    # ── Initialize debug logger (writes to file if enabled) ──
    from backend.services.debug_logger import dlog, is_debug_logging_enabled
    _debug = is_debug_logging_enabled()
    if _debug:
        dlog(f"═══ TICK {tick} START ═══ session={session_id}")

    # ── 0.5. Run Red AI agents (if applicable) ────────────────
    from backend.services.red_ai.runner import run_red_agents
    _t0 = time.monotonic()
    try:
        red_events = await run_red_agents(session_id, tick, db)
        all_events.extend(red_events)
    except Exception as e:
        logger.warning("Red AI runner failed: %s", e)
    _t_red_ai = time.monotonic() - _t0
    if _t_red_ai > 0.5:
        logger.warning("TICK %d: Red AI took %.1fs", tick, _t_red_ai)
    if _debug:
        dlog(f"  [0.5] Red AI: {_t_red_ai:.2f}s, events={len(red_events) if 'red_events' in dir() else '?'}")

    # ── 1. Process pending orders → assign tasks ─────────────────
    _t0 = time.monotonic()
    order_events = await _process_orders(session_id, tick, db)
    all_events.extend(order_events)
    _t_orders = time.monotonic() - _t0
    if _debug:
        dlog(f"  [1] Process orders: {_t_orders:.2f}s, events={len(order_events)}")

    # ── Load all units for this session ──────────────────────────
    _t0 = time.monotonic()
    result = await db.execute(
        select(Unit).where(Unit.session_id == session_id)
    )
    all_units = list(result.scalars().all())
    _t_load = time.monotonic() - _t0
    if _debug:
        dlog(f"  [load] Units loaded: {len(all_units)} in {_t_load:.2f}s")

    blue_units = [u for u in all_units if not u.is_destroyed and u.side.value == "blue"]
    red_units = [u for u in all_units if not u.is_destroyed and u.side.value == "red"]

    # ── 1b. Resolve attack targets from contacts ──────────────
    # For units with attack/engage task but no target_location, find the nearest
    # known enemy contact and set it as the movement target so they advance.
    result_contacts = await db.execute(
        select(Contact).where(Contact.session_id == session_id, Contact.is_stale == False)
    )
    active_contacts = list(result_contacts.scalars().all())

    for unit in all_units:
        if unit.is_destroyed:
            continue
        task = unit.current_task
        if not task:
            continue
        task_type = task.get("type", "")
        if task_type not in ("attack", "engage", "fire"):
            continue
        # Already has a target location — movement engine will handle it
        if task.get("target_location"):
            continue

        unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)

        # Try to find nearest enemy contact for this unit's side
        best_dist = float('inf')
        best_lat = None
        best_lon = None

        for contact in active_contacts:
            c_side = contact.observing_side.value if hasattr(contact.observing_side, 'value') else str(contact.observing_side)
            if c_side != unit_side:
                continue  # This contact is observed by the other side
            if contact.location_estimate is None:
                continue
            try:
                c_pt = to_shape(contact.location_estimate)
                c_lat, c_lon = c_pt.y, c_pt.x
            except Exception:
                continue
            try:
                u_pt = to_shape(unit.position)
                u_lat, u_lon = u_pt.y, u_pt.x
            except Exception:
                continue
            dlat = (c_lat - u_lat) * 111320
            dlon = (c_lon - u_lon) * 74000
            dist = (dlat**2 + dlon**2) ** 0.5
            if dist < best_dist:
                best_dist = dist
                best_lat = c_lat
                best_lon = c_lon

        if best_lat is not None:
            task["target_location"] = {"lat": best_lat, "lon": best_lon}
            unit.current_task = task

    # ── Load map objects (obstacles, structures) ─────────────
    mo_result = await db.execute(
        select(MapObject).where(MapObject.session_id == session_id)
    )
    map_objects_list = list(mo_result.scalars().all())

    # ── 1c. Process transient effect decay (smoke, fog, fire, chemical) ──
    TRANSIENT_TYPES = {"smoke", "fog_effect", "fire_effect", "chemical_cloud"}
    EFFECT_DISSIPATE_LABELS = {
        "smoke": "Smoke screen",
        "fog_effect": "Fog zone",
        "fire_effect": "Area fire",
        "chemical_cloud": "Chemical cloud",
    }
    smoke_updated = []  # transient effect MapObjects whose state changed this tick
    for obj in map_objects_list:
        if obj.object_type in TRANSIENT_TYPES and obj.is_active:
            props = obj.properties or {}
            ticks_remaining = props.get("ticks_remaining", 0) - 1
            if ticks_remaining <= 0:
                obj.is_active = False
                smoke_updated.append(obj)
                eff_label = EFFECT_DISSIPATE_LABELS.get(obj.object_type, obj.object_type)
                all_events.append({
                    "event_type": "effect_dissipated",
                    "text_summary": f"{eff_label} dissipated at {obj.label or 'position'}",
                    "payload": {"object_id": str(obj.id), "effect_type": obj.object_type},
                })
            else:
                new_props = dict(props)
                new_props["ticks_remaining"] = ticks_remaining
                obj.properties = new_props
                smoke_updated.append(obj)

    # ── 1d. Compute weather & night modifiers (used by movement + detection) ──
    weather_mod = 1.0
    weather_movement_mod = 1.0
    night_mod = 1.0
    if scenario and scenario.environment:
        vis_km = scenario.environment.get("visibility_km", 5.0)
        weather_mod = min(1.0, vis_km / 5.0)

        # Weather effects on movement and visibility
        weather_type = scenario.environment.get("weather", "clear")
        precipitation = scenario.environment.get("precipitation", "none")
        if weather_type in ("rain", "rainy"):
            weather_mod *= 0.7
            weather_movement_mod *= 0.8  # mud
        elif weather_type in ("heavy_rain", "storm"):
            weather_mod *= 0.4
            weather_movement_mod *= 0.6  # heavy mud
        elif weather_type == "fog":
            weather_mod *= 0.3
            weather_movement_mod *= 0.95
        elif weather_type == "snow":
            weather_mod *= 0.6
            weather_movement_mod *= 0.7
        if precipitation == "rain":
            weather_mod *= 0.85
            weather_movement_mod *= 0.9
        elif precipitation == "heavy_rain":
            weather_mod *= 0.5
            weather_movement_mod *= 0.7
        elif precipitation == "snow":
            weather_mod *= 0.7
            weather_movement_mod *= 0.75

    # Night-time effects on visibility
    if game_time:
        hour = game_time.hour
        if 21 <= hour or hour < 5:
            # Night: heavy visibility reduction
            night_mod = 0.3
        elif 5 <= hour < 7 or 19 <= hour < 21:
            # Dawn/dusk: moderate visibility reduction
            night_mod = 0.6

    # Combine weather and night modifiers for detection
    combined_visibility_mod = weather_mod * night_mod

    # ── 2. Execute movement (with obstacle effects) ──────────
    _t0 = time.monotonic()
    movement_events = process_movement(all_units, tick_duration, terrain, map_objects_list, weather_movement_mod=weather_movement_mod)
    all_events.extend(movement_events)
    _t_move = time.monotonic() - _t0
    if _debug:
        dlog(f"  [2] Movement: {_t_move:.2f}s, events={len(movement_events)}")

    # ── 2a. Mark completed orders from movement arrivals ──────
    await _complete_orders_from_events(session_id, movement_events, db)

    # ── 2a2. Process conditional/phased orders (order_queue) ──
    cond_events = _process_conditional_orders(all_units, terrain, grid_service)
    all_events.extend(cond_events)

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

    _t0 = time.monotonic()
    new_contacts_data = process_detection(
        blue_units, red_units, tick, terrain, combined_visibility_mod,
        los_service=los_service,
        map_objects=map_objects_list,
        night_mod=night_mod,
    )
    _t_detect = time.monotonic() - _t0
    if _debug:
        dlog(f"  [3] Detection: {_t_detect:.2f}s, new_contacts={len(new_contacts_data)}")

    # Upsert contacts
    _t0 = time.monotonic()
    contact_events = await _upsert_contacts(session_id, tick, game_time, new_contacts_data, db)
    all_events.extend(contact_events)
    _t_upsert = time.monotonic() - _t0
    if _debug:
        dlog(f"  [3b] Upsert contacts: {_t_upsert:.2f}s, events={len(contact_events)}")

    # ── 3b. Map object discovery (LOS-based) ──────────────────
    discovery_events = _process_object_discovery(
        blue_units, red_units, map_objects_list, terrain, los_service,
    )
    all_events.extend(discovery_events)

    # ── 3c. Same-tick target resolution from newly detected contacts ──
    # If Blue detects Red THIS tick, Blue attack units should immediately get
    # target info rather than waiting until next tick's step 1b.
    if new_contacts_data:
        # Reload contacts from DB (now includes this tick's detections)
        _fresh_contacts_result = await db.execute(
            select(Contact).where(Contact.session_id == session_id, Contact.is_stale == False)
        )
        _fresh_contacts = list(_fresh_contacts_result.scalars().all())

        for unit in all_units:
            if unit.is_destroyed:
                continue
            task = unit.current_task
            if not task:
                continue
            task_type = task.get("type", "")
            if task_type not in ("attack", "engage", "fire"):
                continue
            if task.get("target_location"):
                continue  # Already has a target

            unit_side = unit.side.value if hasattr(unit.side, 'value') else str(unit.side)
            best_dist = float('inf')
            best_lat = None
            best_lon = None
            best_target_uid = None

            for contact in _fresh_contacts:
                c_side = contact.observing_side.value if hasattr(contact.observing_side, 'value') else str(contact.observing_side)
                if c_side != unit_side:
                    continue
                if contact.location_estimate is None:
                    continue
                try:
                    c_pt = to_shape(contact.location_estimate)
                    c_lat, c_lon = c_pt.y, c_pt.x
                except Exception:
                    continue
                try:
                    u_pt = to_shape(unit.position)
                    u_lat, u_lon = u_pt.y, u_pt.x
                except Exception:
                    continue
                dlat = (c_lat - u_lat) * 111320
                dlon = (c_lon - u_lon) * 74000
                dist = (dlat**2 + dlon**2) ** 0.5
                if dist < best_dist:
                    best_dist = dist
                    best_lat = c_lat
                    best_lon = c_lon
                    best_target_uid = str(contact.target_unit_id) if contact.target_unit_id else None

            if best_lat is not None:
                new_task = dict(task)
                new_task["target_location"] = {"lat": best_lat, "lon": best_lon}
                if best_target_uid:
                    new_task["target_unit_id"] = best_target_uid
                unit.current_task = new_task

    # ── 4. Decay stale contacts ──────────────────────────────────
    result = await db.execute(
        select(Contact).where(Contact.session_id == session_id)
    )
    existing_contacts = list(result.scalars().all())
    contacts_to_delete, stale_events = process_contacts(existing_contacts, tick)
    all_events.extend(stale_events)
    for c in contacts_to_delete:
        await db.delete(c)

    # ── 4b. Artillery support (auto-assign idle artillery in CoC) ──
    # ── 4b. Artillery support — moved to after 4d so we know which units are under attack ──

    # ── 4c. Defensive posture / dig-in progression ─────────────
    from backend.engine.defense import process_defense
    defense_events = process_defense(all_units, map_objects_list)
    all_events.extend(defense_events)

    # ── 4d. Automatic return fire: units being attacked fire back ──
    # Identify units with attack/engage/fire tasks targeting specific units
    attacking_map = {}  # target_id → [attacker_ids]
    for u in all_units:
        if u.is_destroyed:
            continue
        task = u.current_task
        if not task:
            continue
        task_type = task.get("type", "")
        if task_type in ("attack", "engage", "fire"):
            tid = task.get("target_unit_id")
            if tid:
                attacking_map.setdefault(str(tid), []).append(str(u.id))

    # Units being attacked that don't have a combat task → auto-engage nearest attacker
    # (Skip disengaging units — they are breaking contact on purpose)
    for u in all_units:
        if u.is_destroyed:
            continue
        uid_str = str(u.id)
        if uid_str not in attacking_map:
            continue
        task = u.current_task
        if task and task.get("type") in ("attack", "engage", "fire"):
            continue  # already fighting
        if task and task.get("type") == "disengage":
            continue  # disengaging — do NOT return fire
        # Find the nearest attacker
        attacker_ids = attacking_map[uid_str]
        if attacker_ids:
            # Resolve attacker's position from FOW contacts (not exact server position)
            u_side = u.side.value if hasattr(u.side, 'value') else str(u.side)
            attacker_contact_loc = None
            for contact in existing_contacts:
                if (contact.target_unit_id
                        and str(contact.target_unit_id) == str(attacker_ids[0])):
                    c_side = (contact.observing_side.value
                              if hasattr(contact.observing_side, 'value')
                              else str(contact.observing_side))
                    if c_side == u_side and contact.location_estimate:
                        try:
                            c_pt = to_shape(contact.location_estimate)
                            attacker_contact_loc = {"lat": c_pt.y, "lon": c_pt.x}
                        except Exception:
                            pass
                        break
            engage_task = {
                "type": "engage",
                "target_unit_id": attacker_ids[0],
                "auto_return_fire": True,
            }
            if attacker_contact_loc:
                engage_task["target_location"] = attacker_contact_loc
            u.current_task = engage_task

    # ── 4e. Artillery support (auto-assign idle artillery in CoC) ──
    # Build preliminary under_fire set from attacking_map (who is being targeted)
    preliminary_under_fire = set()
    for target_id_str in attacking_map:
        for u in all_units:
            if str(u.id) == target_id_str and not u.is_destroyed:
                preliminary_under_fire.add(u.id)
                break
    arty_events = process_artillery_support(all_units, terrain, under_fire=preliminary_under_fire)
    all_events.extend(arty_events)

    # ── 5. Execute combat ────────────────────────────────────────
    _t0 = time.monotonic()
    combat_events, under_fire = process_combat(all_units, terrain, map_objects_list,
                                                contacts=existing_contacts)
    all_events.extend(combat_events)
    _t_combat = time.monotonic() - _t0
    if _debug:
        dlog(f"  [5] Combat: {_t_combat:.2f}s, events={len(combat_events)}, under_fire={len(under_fire)}")

    # ── 5a. Mark completed orders from combat (fire salvos expended) ──
    await _complete_orders_from_events(session_id, combat_events, db)

    # ── 5b. Remove contacts referencing destroyed units ────────
    destroyed_ids = {str(u.id) for u in all_units if u.is_destroyed}
    if destroyed_ids:
        result_dc = await db.execute(
            select(Contact).where(Contact.session_id == session_id)
        )
        for contact in result_dc.scalars().all():
            if contact.target_unit_id and str(contact.target_unit_id) in destroyed_ids:
                await db.delete(contact)

        # ── 5c. Clear engage/attack/fire tasks targeting destroyed units ──
        for u in all_units:
            if u.is_destroyed:
                continue
            task = u.current_task
            if not task:
                continue
            task_type = task.get("type", "")
            if task_type not in ("attack", "engage", "fire"):
                continue
            target_uid = task.get("target_unit_id")
            if target_uid and str(target_uid) in destroyed_ids:
                u.current_task = None

    # ── 6. Suppression recovery ──────────────────────────────
    process_suppression_recovery(all_units, under_fire)

    # ── 7. Morale effects ────────────────────────────────────
    morale_events = process_morale(all_units, under_fire, tick_events=all_events)
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

    # ── 9b2. Resupply engine (supply caches within 50m, logistics units, resupply orders) ──
    resupply_events = process_resupply(all_units, map_objects_list)
    all_events.extend(resupply_events)

    # ── 9c. Transient effect damage (fire, chemical cloud) ────
    effect_damage_events = _process_effect_damage(all_units, map_objects_list)
    all_events.extend(effect_damage_events)

    # ── 10. Persist events ───────────────────────────────────────
    for evt_dict in all_events:
        # Determine visibility based on event type and involved units
        vis = _determine_visibility(evt_dict)
        event_row = create_event(session_id, tick, game_time, evt_dict, vis)
        db.add(event_row)

    # ── 10b. Generate reports (SPOTREPs, SHELREPs, CASREPs, SITREPs, INTSUMs) ──
    _t0 = time.monotonic()
    from backend.services.report_generator import generate_tick_reports

    # Determine language from scenario environment
    _lang = "ru"
    if scenario and scenario.environment:
        _lang = scenario.environment.get("language", "ru")

    # Reload contacts for report generation
    _contacts_result = await db.execute(
        select(Contact).where(Contact.session_id == session_id)
    )
    _all_contacts = list(_contacts_result.scalars().all())

    tick_reports = generate_tick_reports(
        all_units=all_units,
        contacts=_all_contacts,
        tick=tick,
        game_time=game_time,
        tick_events=all_events,
        under_fire=under_fire,
        grid_service=grid_service,
        lang=_lang,
    )

    report_broadcast = []
    for rpt in tick_reports:
        to_side_val = rpt.get("to_side", "blue")
        try:
            to_side_enum = ReportSide(to_side_val)
        except ValueError:
            to_side_enum = ReportSide.blue

        from_uid = rpt.get("from_unit_id")
        if from_uid and not isinstance(from_uid, uuid.UUID):
            try:
                from_uid = uuid.UUID(str(from_uid))
            except (ValueError, AttributeError):
                from_uid = None

        report_row = Report(
            session_id=session_id,
            tick=tick,
            game_timestamp=game_time,
            channel=rpt["channel"],
            from_unit_id=from_uid,
            to_side=to_side_enum,
            text=rpt["text"],
            structured_data=rpt.get("structured_data"),
        )
        db.add(report_row)

        report_broadcast.append({
            "type": "report_new",
            "channel": rpt["channel"],
            "to_side": to_side_val,
            "text": rpt["text"],
            "tick": tick,
            "from_unit_id": str(from_uid) if from_uid else None,
            "structured_data": rpt.get("structured_data"),
            "report_id": str(report_row.id),
        })

    # ── 10c. Generate radio chatter (idle requests + peer support) ──
    _t_reports = time.monotonic() - _t0
    if _debug:
        dlog(f"  [10b] Reports: {_t_reports:.2f}s, count={len(tick_reports)}")
    from backend.engine.radio_chatter import (
        generate_idle_radio_messages,
        generate_peer_support_requests,
        generate_casualty_radio_messages,
        generate_contact_radio_messages,
        generate_combat_coordination_messages,
        generate_artillery_fire_messages,
        generate_coordinated_attack_messages,
    )
    from backend.models.chat_message import ChatMessage

    idle_msgs = generate_idle_radio_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )
    peer_msgs = generate_peer_support_requests(
        all_units, under_fire, tick,
        grid_service=grid_service, language=_lang,
    )
    casualty_msgs = generate_casualty_radio_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )
    contact_msgs = generate_contact_radio_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )
    combat_coord_msgs = generate_combat_coordination_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )
    arty_fire_msgs = generate_artillery_fire_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )
    coord_attack_msgs = generate_coordinated_attack_messages(
        all_units, all_events, tick,
        grid_service=grid_service, language=_lang,
    )

    radio_messages = idle_msgs + peer_msgs + casualty_msgs + contact_msgs + combat_coord_msgs + arty_fire_msgs + coord_attack_msgs
    if _debug:
        dlog(f"  [10c] Radio chatter: idle={len(idle_msgs)} peer={len(peer_msgs)} casualty={len(casualty_msgs)} contact={len(contact_msgs)} combat_coord={len(combat_coord_msgs)} arty_fire={len(arty_fire_msgs)} coord_attack={len(coord_attack_msgs)}")
        # Log contact events for debugging detection→radio pipeline
        contact_new_evts = [e for e in all_events if e.get("event_type") in ("contact_new", "contact_refreshed")]
        dlog(f"  [10c] Contact events in all_events: {len(contact_new_evts)}")
        for ce in contact_new_evts[:5]:
            dlog(f"         contact_evt: type={ce.get('event_type')} actor={ce.get('actor_unit_id')} payload_obs={ce.get('payload',{}).get('observing_unit_id')}")
    radio_broadcast = []
    for msg in radio_messages:
        # Ensure sender_name has the 📻 prefix for unit responses
        # so chat history correctly identifies them as unit messages
        raw_name = msg["sender_name"]
        display_name = raw_name if raw_name.startswith("📻") else f"📻 {raw_name}"
        chat = ChatMessage(
            session_id=session_id,
            sender_name=display_name,
            side=msg["side"],
            recipient="all",
            text=msg["text"],
            game_time=game_time,
        )
        db.add(chat)
        radio_broadcast.append({
            "type": "chat_message",
            "sender_name": display_name,
            "side": msg["side"],
            "text": msg["text"],
            "recipient": "all",
            "game_time": _iso_utc(game_time),
            "is_unit_response": msg.get("is_unit_response", True),
            "response_type": msg.get("response_type", ""),
        })

    # ── 11. Advance tick ─────────────────────────────────────────
    session.tick = tick + 1
    session.current_time = game_time + timedelta(seconds=tick_duration)

    # ── 11b. Check turn limit → auto-finish session ──────────────
    turn_limit = 0
    # Check session settings first
    if session.settings and isinstance(session.settings, dict):
        turn_limit = session.settings.get("turn_limit", 0)
    # Fallback: check scenario objectives
    if not turn_limit and scenario and scenario.objectives and isinstance(scenario.objectives, dict):
        turn_limit = scenario.objectives.get("turn_limit", 0)

    game_finished = False
    victory_result = None

    if turn_limit and session.tick >= turn_limit:
        session.status = SessionStatus.finished
        game_finished = True
        all_events.append({
            "event_type": "game_finished",
            "text_summary": f"Turn limit reached ({turn_limit} turns). Game over.",
            "payload": {"reason": "turn_limit", "turn_limit": turn_limit},
        })

    # ── 11c. Evaluate victory conditions (AI referee) ────────────
    if scenario and scenario.objectives and isinstance(scenario.objectives, dict):
        victory_blue_cond = scenario.objectives.get("victory_blue")
        victory_red_cond = scenario.objectives.get("victory_red")
        if victory_blue_cond or victory_red_cond:
            _t0_vc = time.monotonic()
            victory_result = await _evaluate_victory_conditions(
                session=session,
                scenario=scenario,
                all_units=all_units,
                all_contacts=_all_contacts,
                tick=session.tick,
                game_time=session.current_time,
                grid_service=grid_service,
                victory_blue_cond=victory_blue_cond,
                victory_red_cond=victory_red_cond,
            )
            _t_vc = time.monotonic() - _t0_vc
            if _t_vc > 0.5:
                logger.warning("TICK %d: Victory eval took %.1fs", session.tick, _t_vc)
            if _debug:
                dlog(f"  [11c] Victory eval: {_t_vc:.2f}s, result={'winner=' + str(victory_result.get('winner')) if victory_result else 'none'}")
            if victory_result and victory_result.get("winner"):
                if not game_finished:
                    session.status = SessionStatus.finished
                    game_finished = True
                all_events.append({
                    "event_type": "game_finished",
                    "text_summary": victory_result.get("summary", "Victory conditions met."),
                    "payload": {
                        "reason": "victory_condition",
                        "winner": victory_result["winner"],
                        "detail": victory_result.get("detail", ""),
                    },
                })

    await db.flush()

    _tick_total = time.monotonic() - _tick_start
    if _tick_total > 2.0:
        logger.warning("TICK %d total: %.1fs (slow!)", session.tick, _tick_total)
    else:
        logger.info("TICK %d total: %.1fs", session.tick, _tick_total)
    if _debug:
        dlog(f"═══ TICK {session.tick - 1} END ═══ total={_tick_total:.2f}s events={len(all_events)} radio={len(radio_messages)} reports={len(tick_reports)}")

    return {
        "tick": session.tick,
        "game_time": _iso_utc(session.current_time),
        "events_count": len(all_events),
        "units_alive": sum(1 for u in all_units if not u.is_destroyed),
        "radio_messages": radio_broadcast,
        "reports": report_broadcast,
        "_raw_events": all_events,  # for combat impact visualization
        "_smoke_updated": smoke_updated,  # smoke MapObjects whose state changed
    }


async def _process_orders(
    session_id: uuid.UUID,
    tick: int,
    db: AsyncSession,
) -> list[dict]:
    """
    Process pending/validated orders and assign tasks to units.

    Order precedence: newer orders for the same unit override older ones.
    Halt orders clear the unit's current_task.
    Speed from order data is applied to unit.move_speed_mps.
    """
    from backend.api.units import UNIT_TYPE_SPEEDS, DEFAULT_SPEEDS

    events = []

    result = await db.execute(
        select(Order).where(
            Order.session_id == session_id,
            Order.status.in_([OrderStatus.pending, OrderStatus.validated]),
        ).order_by(Order.issued_at.asc())  # older first, newer overrides
    )
    orders = list(result.scalars().all())

    if not orders:
        return events

    # Group orders by target unit — latest order wins for each unit
    unit_orders: dict[str, tuple[Order, dict | None]] = {}  # unit_id → (order, task)

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

        # Map this order to each target unit (latest order wins)
        if order.target_unit_ids:
            for unit_id in order.target_unit_ids:
                uid_str = str(unit_id)
                unit_orders[uid_str] = (order, task)
        else:
            # No specific target — mark executing anyway
            order.status = OrderStatus.executing

    # Now assign tasks to units
    processed_order_ids = set()
    for uid_str, (order, task) in unit_orders.items():
        try:
            unit_uuid = uuid.UUID(uid_str)
        except ValueError:
            continue

        result = await db.execute(
            select(Unit).where(
                Unit.id == unit_uuid,
                Unit.session_id == session_id,
                Unit.is_destroyed == False,
            )
        )
        unit = result.scalar_one_or_none()
        if not unit:
            continue

        task_type = task.get("type", "")

        if task_type == "halt":
            # Halt: clear current task
            unit.current_task = None
            events.append({
                "event_type": "order_issued",
                "actor_unit_id": unit_uuid,
                "text_summary": f"{unit.name} halts",
                "payload": {"order_id": str(order.id), "task": task},
            })
        elif task_type == "disengage":
            # Disengage: stop all combat, seek nearest covered position
            # Set fast speed for withdrawal
            speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
            unit.move_speed_mps = speeds.get("fast", speeds.get("slow", 3.0))
            unit.current_task = {
                "type": "disengage",
                "order_id": str(order.id),
                "disengaging": True,
            }
            events.append({
                "event_type": "order_issued",
                "actor_unit_id": unit_uuid,
                "text_summary": f"{unit.name} disengaging, breaking contact",
                "payload": {"order_id": str(order.id), "task": task},
            })
        else:
            # Assign the task
            unit.current_task = task

            # Apply move speed from order if specified
            speed_label = task.get("speed")
            if speed_label and task_type in ("move", "attack", "advance", "resupply"):
                speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
                if speed_label in speeds:
                    unit.move_speed_mps = speeds[speed_label]
            # Default fast speed for resupply movement (get there quickly)
            elif task_type == "resupply" and not speed_label:
                speeds = UNIT_TYPE_SPEEDS.get(unit.unit_type, DEFAULT_SPEEDS)
                unit.move_speed_mps = speeds.get("fast", speeds.get("slow", 3.0))

            # Apply formation if specified in the order
            formation = task.get("formation")
            if formation:
                caps = dict(unit.capabilities or {})
                caps["formation"] = formation
                unit.capabilities = caps

            # Handle phased/conditional orders: if order has "phases",
            # store subsequent phases in unit.order_queue
            phases = task.get("phases") or (order.parsed_order or {}).get("phases")
            if phases and isinstance(phases, list) and len(phases) > 1:
                # First phase is already the current task
                # Store remaining phases as conditional order queue
                unit.order_queue = phases[1:]

            events.append({
                "event_type": "order_issued",
                "actor_unit_id": unit_uuid,
                "text_summary": f"{unit.name} received order: {task_type}" + (
                    f" ({len(phases) - 1} conditional follow-up)" if phases and len(phases) > 1 else ""
                ),
                "payload": {"order_id": str(order.id), "task": task},
            })

        processed_order_ids.add(str(order.id))

    # Mark all processed orders as executing
    for order in orders:
        if str(order.id) in processed_order_ids:
            order.status = OrderStatus.executing
        elif order.status == OrderStatus.validated and order not in [o for o, _ in unit_orders.values()]:
            # Orders that weren't assigned to any unit (no valid targets)
            pass  # Keep as validated for next tick

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
            result_task = {
                "type": task_type,
                "target_location": target_loc,
                "order_id": str(order.id),
            }
            # Add salvos for fire tasks
            if task_type == "fire":
                from backend.engine.combat import DEFAULT_FIRE_SALVOS
                result_task["salvos_remaining"] = intent.get("salvos", DEFAULT_FIRE_SALVOS)
            return result_task

    # Try parsed order
    if order.parsed_order:
        po = order.parsed_order
        task_type = po.get("order_type", po.get("type", order.order_type))
        target_loc = po.get("target_location", po.get("destination"))
        target_unit = po.get("target_unit_id")
        target_snail = po.get("target_snail")
        speed = po.get("speed")
        task = {"type": task_type, "order_id": str(order.id)}
        if target_loc:
            task["target_location"] = target_loc
        if target_unit:
            task["target_unit_id"] = target_unit
        if target_snail:
            task["target_snail"] = target_snail
        if speed:
            task["speed"] = speed
        # Add salvos for fire tasks (default 3 unless specified)
        if task_type == "fire":
            from backend.engine.combat import DEFAULT_FIRE_SALVOS
            task["salvos_remaining"] = po.get("salvos", DEFAULT_FIRE_SALVOS)
        return task

    # Fallback: try to parse simple keywords from original text
    if order.original_text:
        text = order.original_text.lower()
        if "halt" in text or "stop" in text:
            return {"type": "halt", "order_id": str(order.id)}
        if any(kw in text for kw in ["disengage", "break contact", "разорвать контакт", "выйти из боя"]):
            return {"type": "disengage", "order_id": str(order.id)}
        if any(kw in text for kw in ["fire at", "fire on", "fire mission", "огонь по", "стреляй",
                                      "artillery support", "fire support", "support fire",
                                      "артподдержк", "огневая поддержк"]):
            from backend.engine.combat import DEFAULT_FIRE_SALVOS
            return {"type": "fire", "order_id": str(order.id), "salvos_remaining": DEFAULT_FIRE_SALVOS}
        if "move" in text or "advance" in text:
            return {"type": "move", "order_id": str(order.id)}
        if "attack" in text or "engage" in text:
            return {"type": "attack", "order_id": str(order.id)}
        if "defend" in text or "hold" in text:
            return {"type": "defend", "order_id": str(order.id)}
        if "observe" in text or "recon" in text:
            return {"type": "observe", "order_id": str(order.id)}
        if any(kw in text for kw in ["resupply", "re-supply", "rearm", "ammo",
                                       "пополн", "боеприпас", "снабж", "боекомплект"]):
            return {"type": "resupply", "order_id": str(order.id)}

    return None


async def _complete_orders_from_events(
    session_id: uuid.UUID,
    events: list[dict],
    db: AsyncSession,
) -> None:
    """
    Mark orders as completed when their associated tasks finish.

    Looks for 'order_completed' events with order_id in the payload
    and updates the corresponding Order status.
    """
    from datetime import datetime, timezone

    for evt in events:
        if evt.get("event_type") != "order_completed":
            continue
        payload = evt.get("payload", {})
        # Check if the unit that completed had an order_id in its task
        actor_id = evt.get("actor_unit_id")
        if actor_id:
            # Find executing orders for this unit
            result = await db.execute(
                select(Order).where(
                    Order.session_id == session_id,
                    Order.status == OrderStatus.executing,
                    Order.target_unit_ids.any(actor_id),
                )
            )
            for order in result.scalars().all():
                order.status = OrderStatus.completed
                order.completed_at = datetime.now(timezone.utc)


async def _upsert_contacts(
    session_id: uuid.UUID,
    tick: int,
    game_time: datetime,
    new_contacts_data: list[dict],
    db: AsyncSession,
) -> list[dict]:
    """
    Create or update contacts from detection results.
    Batch-loads existing contacts once for efficiency.
    """
    events = []
    if not new_contacts_data:
        return events

    # Batch-load ALL existing contacts for this session once (not per-detection)
    result = await db.execute(
        select(Contact).where(Contact.session_id == session_id)
    )
    all_existing = list(result.scalars().all())

    # Index by (observing_side, target_unit_id) for O(1) lookup
    existing_by_target: dict[tuple[str, str], Contact] = {}
    for contact in all_existing:
        obs_side = contact.observing_side.value if hasattr(contact.observing_side, 'value') else str(contact.observing_side)
        if contact.target_unit_id:
            key = (obs_side, str(contact.target_unit_id))
            existing_by_target[key] = contact

    for cd in new_contacts_data:
        target_uid = cd.get("target_unit_id")
        obs_side = cd["observing_side"]

        # Fast lookup for existing contact matching this target
        updated = False
        if target_uid:
            key = (obs_side, str(target_uid))
            contact = existing_by_target.get(key)
            if contact:
                was_stale = contact.is_stale
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
                if was_stale:
                    events.append({
                        "event_type": "contact_refreshed",
                        "actor_unit_id": cd.get("observing_unit_id"),
                        "text_summary": f"Contact re-acquired: {cd.get('estimated_type', 'unknown')}",
                        "payload": {
                            "observing_side": cd["observing_side"],
                            "observing_unit_id": str(cd["observing_unit_id"]) if cd.get("observing_unit_id") else None,
                            "estimated_type": cd.get("estimated_type"),
                            "lat": cd["lat"],
                            "lon": cd["lon"],
                            "confidence": cd["confidence"],
                        },
                    })

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

            # Add to index for deduplication within this batch
            if target_uid:
                existing_by_target[(obs_side, str(target_uid))] = contact

            events.append({
                "event_type": "contact_new",
                "actor_unit_id": cd.get("observing_unit_id"),
                "text_summary": f"New contact: {cd.get('estimated_type', 'unknown')} detected",
                "payload": {
                    "observing_side": cd["observing_side"],
                    "observing_unit_id": str(cd["observing_unit_id"]) if cd.get("observing_unit_id") else None,
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
    # Red AI events visible to admin only (for after-action review)
    if etype in ("red_ai_decision", "red_ai_error"):
        return "admin"
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


def _process_conditional_orders(
    all_units: list,
    terrain,
    grid_service,
) -> list[dict]:
    """
    Check each unit's order_queue for conditional triggers.
    If condition is met, pop the entry and assign the task.

    Supported conditions:
      - task_completed: unit has no current_task (previous task finished)
      - location_reached: unit is at the specified snail_path
    """
    events = []

    for unit in all_units:
        if unit.is_destroyed:
            continue
        queue = unit.order_queue
        if not queue or not isinstance(queue, list) or len(queue) == 0:
            continue

        entry = queue[0]
        condition = entry.get("condition", {})
        cond_type = condition.get("type", "task_completed")
        met = False

        if cond_type == "task_completed":
            # Trigger when unit has no current task
            met = unit.current_task is None

        elif cond_type == "location_reached":
            # Trigger when unit is at the specified snail path
            target_snail = condition.get("snail_path", "")
            if target_snail and grid_service and unit.position is not None:
                try:
                    from geoalchemy2.shape import to_shape
                    pt = to_shape(unit.position)
                    current_snail = grid_service.point_to_snail(pt.y, pt.x, depth=target_snail.count("-"))
                    if current_snail and current_snail == target_snail:
                        met = True
                except Exception:
                    pass

        if met:
            task = entry.get("task")
            if task:
                unit.current_task = task
                events.append({
                    "event_type": "conditional_order_activated",
                    "actor_unit_id": unit.id,
                    "text_summary": f"{unit.name}: conditional order triggered — {task.get('type', 'unknown')}",
                    "payload": {
                        "condition": condition,
                        "task": task,
                    },
                })
            # Pop the entry from the queue
            new_queue = queue[1:]
            unit.order_queue = new_queue if new_queue else None

    return events


async def _evaluate_victory_conditions(
    session,
    scenario,
    all_units: list,
    all_contacts: list,
    tick: int,
    game_time,
    grid_service,
    victory_blue_cond: str | None,
    victory_red_cond: str | None,
) -> dict | None:
    """
    Use LLM to evaluate open-text victory conditions against current game state.

    Returns:
        {"winner": "blue"|"red"|None, "summary": str, "detail": str}
        or None if evaluation fails or no conditions met.
    """
    import logging
    logger = logging.getLogger(__name__)

    # Only evaluate every 5 ticks, starting after tick 10 (let game develop first)
    if tick < 10 or tick % 5 != 0:
        return None

    # Build game state summary for the AI referee
    blue_alive = [u for u in all_units if not u.is_destroyed and u.side.value == "blue"]
    red_alive = [u for u in all_units if not u.is_destroyed and u.side.value == "red"]

    # Unit summaries with grid refs
    def _unit_summary(u):
        info = f"{u.name} ({u.unit_type}, str={u.strength:.0%})"
        if grid_service and u.position:
            try:
                pt = to_shape(u.position)
                snail = grid_service.point_to_snail(pt.y, pt.x, depth=2)
                if snail:
                    info += f" at {snail}"
            except Exception:
                pass
        task = u.current_task
        if task:
            info += f" [task: {task.get('type', '?')}]"
        return info

    blue_summary = "\n".join(f"  - {_unit_summary(u)}" for u in blue_alive) or "  (none)"
    red_summary = "\n".join(f"  - {_unit_summary(u)}" for u in red_alive) or "  (none)"

    blue_destroyed = [u for u in all_units if u.is_destroyed and u.side.value == "blue"]
    red_destroyed = [u for u in all_units if u.is_destroyed and u.side.value == "red"]

    scenario_desc = scenario.description or "No description"
    mission = ""
    if scenario.objectives and isinstance(scenario.objectives, dict):
        mission = scenario.objectives.get("mission", "") or ""

    prompt = f"""You are an AI referee for a tactical military exercise. Evaluate whether victory conditions have been met.

## Scenario
Title: {scenario.title}
Description: {scenario_desc}
Mission: {mission}
Current Turn: {tick}
Game Time: {game_time.isoformat() if game_time else 'unknown'}

## Blue Forces (alive: {len(blue_alive)}, destroyed: {len(blue_destroyed)}):
{blue_summary}

## Red Forces (alive: {len(red_alive)}, destroyed: {len(red_destroyed)}):
{red_summary}

## Victory Conditions

BLUE wins if: {victory_blue_cond or 'Not specified'}
RED wins if: {victory_red_cond or 'Not specified'}

## Instructions
Evaluate the current game state against the victory conditions.
- Consider unit positions, strength, destroyed units, and current tasks.
- Be conservative — only declare a winner if the condition is CLEARLY and FULLY met.
- If a condition says "all enemy units eliminated", ALL enemy units must be destroyed (strength=0 or is_destroyed).
- If a condition references a location/grid area, check if the required units are actually at that position.
- If neither condition is met yet, set winner to null.

Respond with ONLY a valid JSON object:
{{"winner": "blue" | "red" | null, "summary": "one-line description", "detail": "brief explanation"}}
"""

    try:
        from backend.config import settings
        if not settings.OPENAI_API_KEY:
            return None

        from openai import AsyncOpenAI
        import json

        client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
        create_kwargs = dict(
            model=settings.OPENAI_MODEL_NANO or "gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_completion_tokens=200,
        )
        try:
            response = await client.chat.completions.create(**create_kwargs)
        except Exception as api_err:
            err_str = str(api_err)
            if "max_tokens" in err_str or "max_completion_tokens" in err_str:
                create_kwargs.pop("max_completion_tokens", None)
                response = await client.chat.completions.create(**create_kwargs)
            else:
                raise

        raw = response.choices[0].message.content
        if not raw:
            return None

        result = json.loads(raw)
        winner = result.get("winner")
        if winner and winner in ("blue", "red"):
            logger.info("AI Referee: %s wins! %s", winner, result.get("summary", ""))
            return result
        return None

    except Exception as e:
        logger.warning("Victory condition evaluation failed: %s", e)
        return None


def _process_effect_damage(all_units: list, map_objects: list) -> list[dict]:
    """Apply damage from active fire/chemical effects to units inside them."""
    from backend.engine.map_objects import MAP_OBJECT_DEFS
    events = []
    if not map_objects:
        return events

    DAMAGING_EFFECTS = {"fire_effect", "chemical_cloud"}
    active_effects = [
        o for o in map_objects
        if o.object_type in DAMAGING_EFFECTS and o.is_active and o.geometry is not None
    ]
    if not active_effects:
        return events

    from shapely.geometry import Point as ShapelyPoint
    from geoalchemy2.shape import to_shape as ts

    for unit in all_units:
        if unit.is_destroyed:
            continue
        if unit.position is None:
            continue
        try:
            unit_shape = ts(unit.position)
            unit_pt = ShapelyPoint(unit_shape.x, unit_shape.y)
        except Exception:
            continue

        for eff_obj in active_effects:
            try:
                eff_shape = ts(eff_obj.geometry)
                if not (eff_shape.contains(unit_pt) or eff_shape.distance(unit_pt) * 111320 < 10):
                    continue
            except Exception:
                continue

            defn = MAP_OBJECT_DEFS.get(eff_obj.object_type, {})
            # Differentiated damage for infantry vs vehicle
            is_vehicle = unit.unit_type and any(
                k in unit.unit_type for k in ("tank", "mech", "vehicle", "avlb", "apc")
            )
            if is_vehicle:
                dmg = defn.get("damage_per_tick_vehicle", defn.get("damage_per_tick", 0))
            else:
                dmg = defn.get("damage_per_tick_infantry", defn.get("damage_per_tick", 0))

            if dmg > 0 and unit.strength > 0:
                unit.strength = max(0.0, unit.strength - dmg)
                eff_label = {
                    "fire_effect": "fire zone",
                    "chemical_cloud": "chemical contamination",
                }.get(eff_obj.object_type, eff_obj.object_type)
                events.append({
                    "event_type": "effect_damage",
                    "text_summary": f"{unit.name} taking damage from {eff_label}",
                    "actor_unit_id": str(unit.id),
                    "payload": {
                        "effect_type": eff_obj.object_type,
                        "damage": round(dmg, 4),
                        "unit_strength": round(unit.strength, 3),
                    },
                })
                if unit.strength <= 0:
                    unit.is_destroyed = True
                    events.append({
                        "event_type": "unit_destroyed",
                        "text_summary": f"{unit.name} destroyed by {eff_label}",
                        "actor_unit_id": str(unit.id),
                        "payload": {"cause": eff_obj.object_type},
                    })
    return events
