"""
OrderService – main orchestrator for the order-processing pipeline.

Flow:
  1. Save Order row (status=pending)
  2. Call OrderParser → classification + parsed data
  3. Route by classification:
     - command → LocationResolver → IntentInterpreter → build engine task → validate → set status
     - status_request → generate status report response
     - acknowledgment / status_report → log, mark completed
     - unclear → request clarification response
  4. Generate unit radio response(s)
  5. Persist parsed_order, parsed_intent, LocationReference rows
  6. Broadcast order_status + unit_radio_response via WS
"""

from __future__ import annotations

import logging
import math
import uuid
from datetime import datetime, timezone
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, LineString, Polygon
from sqlalchemy import select, and_, or_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.order import Order, OrderStatus
from backend.models.order import LocationReference, ReferenceType
from backend.models.unit import Unit
from backend.models.grid import GridDefinition
from backend.schemas.order import (
    ParsedOrderData,
    MessageClassification,
    OrderParseResult,
    ResolvedLocation,
    UnitRadioResponse,
    ResponseType,
)
from backend.services.order_parser import order_parser
from backend.services.location_resolver import LocationResolver
from backend.services.intent_interpreter import intent_interpreter
from backend.services.response_generator import response_generator

logger = logging.getLogger(__name__)

_M_PER_DEG_LAT = 111_320.0
_M_PER_DEG_LON_48 = 74_000.0

# ── Language persistence per session+side ──────────────────
# Tracks the last detected language per (session_id, side) to maintain
# language consistency: if the last order was in Russian, all unit
# responses stay in Russian until the next order arrives in English.
_session_language: dict[str, str] = {}  # key → "en" or "ru"

# ── Elevation peaks: delegates to terrain.py's 3-tier cache ──
import time as _time


def _get_session_lang_key(session_id: uuid.UUID, side: str) -> str:
    return f"{session_id}:{side}"


def get_session_language(session_id: uuid.UUID, side: str) -> str | None:
    """Get the persisted language for a session+side (or None if not set)."""
    return _session_language.get(_get_session_lang_key(session_id, side))


def set_session_language(session_id: uuid.UUID, side: str, lang: str):
    """Set the language for a session+side."""
    _session_language[_get_session_lang_key(session_id, side)] = lang


class OrderService:
    """
    Orchestrates the full order-processing pipeline.
    """

    async def process(
        self,
        order: Order,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str = "blue",
    ) -> OrderParseResult:
        """
        Process an order through the full pipeline.

        Args:
            order: The Order ORM object (already saved with status=pending).
            session_id: Session UUID.
            db: Async database session.
            issuer_side: Side of the order issuer.

        Returns:
            OrderParseResult with all pipeline outputs.
        """
        original_text = order.original_text or ""

        # ── 1. Gather context (parallel batch 1) ──────────────────
        import asyncio as _aio
        (
            units_context,
            (grid_info, grid_service),
            game_time,
            map_objects_context,
            elevation_peaks,
            _session_scenario,
        ) = await _aio.gather(
            self._load_units_context(session_id, db),
            self._load_grid(session_id, db),
            self._get_game_time(session_id, db),
            self._load_map_objects_context(session_id, db, issuer_side=issuer_side),
            self._load_elevation_peaks(session_id, db),
            self._load_session_and_scenario(session_id, db),
        )
        if elevation_peaks and grid_info:
            grid_info["height_tops"] = [
                {"label": p["label"], "label_ru": p["label_ru"],
                 "elevation_m": p["elevation_m"], "snail_path": p.get("snail_path", "")}
                for p in elevation_peaks[:30]
            ]

        # ── 1b. Build enriched context for LLM (parallel batch 2) ─
        (
            terrain_ctx,
            contacts_ctx,
            objectives_ctx,
            environment_ctx,
            orders_ctx,
            radio_ctx,
            reports_ctx,
        ) = await _aio.gather(
            self._build_terrain_context(session_id, db, units_context, issuer_side),
            self._build_contacts_context(session_id, db, issuer_side, grid_service=grid_service),
            self._build_objectives_context(session_id, db, _cached=_session_scenario),
            self._build_environment_context(session_id, db, _cached=_session_scenario),
            self._build_orders_history_context(session_id, db, issuer_side, units_context),
            self._build_radio_context(session_id, db, issuer_side),
            self._build_reports_context(session_id, db, issuer_side, units_context),
        )
        friendly_ctx = self._build_friendly_status_context(units_context, issuer_side)
        map_objects_ctx = self._build_map_objects_prompt_context(map_objects_context)

        # ── 2. Parse via LLM ────────────────────────────────────
        parsed = await order_parser.parse(
            original_text=original_text,
            units=units_context,
            grid_info=grid_info,
            game_time=game_time,
            issuer_side=issuer_side,
            terrain_context=terrain_ctx,
            contacts_context=contacts_ctx,
            objectives_context=objectives_ctx,
            friendly_status_context=friendly_ctx,
            environment_context=environment_ctx,
            orders_context=orders_ctx,
            radio_context=radio_ctx,
            reports_context=reports_ctx,
            map_objects_context=map_objects_ctx,
        )

        # ── 2b. Language consistency: enforce last-used language ─────
        # If the new message has a definitive language signal, update the stored
        # language. Otherwise, override the detected language with the stored one.
        from backend.schemas.order import DetectedLanguage
        stored_lang = get_session_language(session_id, issuer_side)
        detected_lang = parsed.language.value  # "en" or "ru"

        # Check if the original text has explicit language signal
        has_cyrillic = any('\u0400' <= c <= '\u04ff' for c in original_text)
        has_latin_alpha = any('a' <= c.lower() <= 'z' for c in original_text)

        if has_cyrillic and not has_latin_alpha:
            # Definitively Russian → update stored language
            set_session_language(session_id, issuer_side, "ru")
            parsed.language = DetectedLanguage.ru
        elif has_latin_alpha and not has_cyrillic:
            # Definitively English → update stored language
            set_session_language(session_id, issuer_side, "en")
            parsed.language = DetectedLanguage.en
        elif stored_lang:
            # Mixed or ambiguous → use stored language to prevent mixing
            parsed.language = DetectedLanguage(stored_lang)

        # ── 2c. Post-LLM standby safety check ─────────────────
        # If the LLM returned fire/attack but original text contains standby
        # keywords, override to observe. LLMs frequently ignore standby context.
        if parsed.classification == MessageClassification.command and parsed.order_type:
            from backend.schemas.order import OrderType
            if parsed.order_type.value == "fire":
                text_lower = original_text.lower()
                _standby_kw = [
                    "get ready", "stand by", "standby", "be ready", "on request",
                    "on call", "when called", "when requested", "prepare to support",
                    "ready to support", "prepare for support",
                    "готовность", "готовьтесь", "будьте готовы", "по запросу",
                    "по вызову", "по команде", "ожидайте", "ждите",
                    "приготовьтесь", "приготовиться", "в готовности",
                ]
                if any(kw in text_lower for kw in _standby_kw):
                    logger.info(
                        "OrderService: LLM returned fire but text has standby keywords → overriding to observe"
                    )
                    parsed.order_type = OrderType.observe

        # Save parsed order immediately
        order.parsed_order = parsed.model_dump(mode="json", exclude_none=True)

        # ── 3. Route by classification ──────────────────────────
        result = OrderParseResult(parsed=parsed)

        if parsed.classification == MessageClassification.command:
            await self._process_command(order, parsed, result, units_context,
                                        grid_service, session_id, db, issuer_side,
                                        map_objects=map_objects_context)

        elif parsed.classification == MessageClassification.status_request:
            await self._process_status_request(order, parsed, result, units_context,
                                                session_id, db, issuer_side, grid_service)

        elif parsed.classification == MessageClassification.acknowledgment:
            order.status = OrderStatus.completed
            order.completed_at = datetime.now(timezone.utc)
            # Generate brief unit ack response — even ack messages deserve a reply
            await self._generate_brief_ack_responses(
                order, parsed, result, units_context, session_id, db, issuer_side, grid_service,
            )

        elif parsed.classification == MessageClassification.status_report:
            order.status = OrderStatus.completed
            order.completed_at = datetime.now(timezone.utc)
            # Generate brief unit ack response
            await self._generate_brief_ack_responses(
                order, parsed, result, units_context, session_id, db, issuer_side, grid_service,
            )

        elif parsed.classification == MessageClassification.unclear:
            # ── Escalate: try full model with richer context before giving up ──
            escalated = False
            try:
                from backend.config import settings
                if settings.OPENAI_API_KEY and parsed.confidence < 0.5:
                    logger.info("Order unclear (conf=%.2f), escalating to full model %s",
                                parsed.confidence, settings.OPENAI_MODEL)
                    reparsed = await order_parser.parse(
                        original_text=original_text,
                        units=units_context,
                        grid_info=grid_info,
                        game_time=game_time,
                        issuer_side=issuer_side,
                        force_full_model=True,
                        terrain_context=terrain_ctx,
                        contacts_context=contacts_ctx,
                        objectives_context=objectives_ctx,
                        friendly_status_context=friendly_ctx,
                        environment_context=environment_ctx,
                        orders_context=orders_ctx,
                        radio_context=radio_ctx,
                        reports_context=reports_ctx,
                        map_objects_context=map_objects_ctx,
                    )
                    if reparsed.classification != MessageClassification.unclear:
                        # Escalation succeeded — re-route
                        parsed = reparsed
                        # Re-apply language consistency
                        if stored_lang:
                            parsed.language = DetectedLanguage(stored_lang)
                        order.parsed_order = parsed.model_dump(mode="json", exclude_none=True)
                        result.parsed = parsed
                        escalated = True
                        if parsed.classification == MessageClassification.command:
                            await self._process_command(order, parsed, result, units_context,
                                                        grid_service, session_id, db, issuer_side,
                                                        map_objects=map_objects_context)
                        elif parsed.classification == MessageClassification.status_request:
                            await self._process_status_request(order, parsed, result, units_context,
                                                                session_id, db, issuer_side, grid_service)
                        elif parsed.classification == MessageClassification.acknowledgment:
                            order.status = OrderStatus.completed
                            order.completed_at = datetime.now(timezone.utc)
                        elif parsed.classification == MessageClassification.status_report:
                            order.status = OrderStatus.completed
                            order.completed_at = datetime.now(timezone.utc)
            except Exception as e:
                logger.warning("Escalation to full model failed: %s", e)

            if not escalated:
                order.status = OrderStatus.failed
                # Generate clarification request from target units (or first available unit)
                matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)
                if not matched:
                    # No specific units referenced — pick first available unit on issuer's side
                    same_side = [
                        u for u in units_context
                        if u.get("side") == issuer_side
                        and not u.get("is_destroyed")
                        and u.get("comms_status") != "offline"
                    ]
                    if same_side:
                        matched = [same_side[0]]
                for unit_dict in matched:
                    resp = response_generator.generate_response(
                        parsed=parsed,
                        unit=unit_dict,
                        response_type=ResponseType.clarify,
                    )
                    if resp:
                        result.responses.append(resp)

        await db.flush()
        return result

    async def _process_command(
        self,
        order: Order,
        parsed: ParsedOrderData,
        result: OrderParseResult,
        units_context: list[dict],
        grid_service: Any,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        map_objects: list[dict] | None = None,
    ):
        """Process a command-type order through the full pipeline."""

        # ── Match target units ────────────────────────────
        matched_units = []

        # Prefer explicit recipients from the order payload/UI when present.
        if order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched_units.append(u)
        if not matched_units:
            matched_units = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        result.matched_unit_ids = [u["id"] for u in matched_units]

        # If still no units, mark failed and generate clarification response
        if not matched_units:
            order.status = OrderStatus.failed
            order.parsed_intent = {"error": "no_target_units_matched"}
            # Generate a clarification response so the user gets feedback
            same_side = [
                u for u in units_context
                if u.get("side") == issuer_side
                and not u.get("is_destroyed")
                and u.get("comms_status") != "offline"
            ]
            if same_side:
                resp = response_generator.generate_response(
                    parsed=parsed,
                    unit=same_side[0],
                    response_type=ResponseType.clarify,
                )
                if resp:
                    result.responses.append(resp)
            return

        # Update order.target_unit_ids from matched units (override if LLM found them)
        if not order.target_unit_ids and matched_units:
            order.target_unit_ids = [uuid.UUID(u["id"]) for u in matched_units]

        if parsed.order_type and parsed.order_type.value in ("split", "merge"):
            handled = await self._process_reorganization_command(
                order=order,
                parsed=parsed,
                result=result,
                matched_units=matched_units,
                units_context=units_context,
                session_id=session_id,
                db=db,
                issuer_side=issuer_side,
            )
            if handled:
                return

        # ── Resolve locations ─────────────────────────────
        # Load elevation peaks for height reference resolution
        elevation_peaks = await self._load_elevation_peaks(session_id, db)
        resolver = LocationResolver(grid_service=grid_service, elevation_peaks=elevation_peaks,
                                    map_objects=map_objects)
        # Use first matched unit's position for relative references
        unit_pos = None
        unit_heading = None
        if matched_units:
            u = matched_units[0]
            if u.get("lat") is not None and u.get("lon") is not None:
                unit_pos = (u["lat"], u["lon"])
            unit_heading = u.get("heading_deg")

        resolved = resolver.resolve_all(
            parsed.location_refs,
            unit_position=unit_pos,
            unit_heading_deg=unit_heading,
        )
        result.resolved_locations = resolved

        # ── Resolve "contact_target" refs from known enemy contacts ──
        # "на цель" / "at the target" → nearest known enemy contact
        has_contact_target = any(loc.ref_type == "contact_target" for loc in resolved)
        needs_request_fire_target = (
            parsed.order_type is not None
            and parsed.order_type.value == "request_fire"
            and not any(loc.lat is not None and loc.lon is not None for loc in resolved)
        )
        best_contact_uid = None
        if (has_contact_target or needs_request_fire_target) and unit_pos:
            try:
                from backend.models.contact import Contact
                from sqlalchemy import select as _select

                contacts_result = await db.execute(
                    _select(Contact).where(
                        Contact.session_id == session_id,
                        Contact.is_stale == False,
                        Contact.observing_side == order.issued_by_side,
                    )
                )
                nearby_contacts = list(contacts_result.scalars().all())
                best_contact = self._find_nearest_contact_target(
                    unit_pos[0],
                    unit_pos[1],
                    nearby_contacts,
                )

                if best_contact is not None:
                    best_contact_lat = best_contact["lat"]
                    best_contact_lon = best_contact["lon"]
                    best_contact_uid = best_contact["target_unit_id"]
                    if has_contact_target:
                        # Update the contact_target resolved location with actual coordinates
                        for i, loc in enumerate(resolved):
                            if loc.ref_type == "contact_target":
                                resolved[i] = ResolvedLocation(
                                    source_text=loc.source_text,
                                    ref_type="contact_target",
                                    normalized_ref=f"contact@{best_contact_lat:.6f},{best_contact_lon:.6f}",
                                    lat=best_contact_lat,
                                    lon=best_contact_lon,
                                    confidence=0.7,
                                )
                                break
                    elif needs_request_fire_target:
                        resolved.append(
                            ResolvedLocation(
                                source_text="current contact",
                                ref_type="contact_target",
                                normalized_ref=f"contact@{best_contact_lat:.6f},{best_contact_lon:.6f}",
                                lat=best_contact_lat,
                                lon=best_contact_lon,
                                confidence=0.7,
                            )
                        )
                    logger.info(
                        "Resolved contact_target to (%f, %f) dist=%.0fm",
                        best_contact_lat, best_contact_lon, best_contact["distance_m"],
                    )
            except Exception as e:
                logger.warning("Failed to resolve contact_target: %s", e)

        # Persist LocationReference rows
        for loc in resolved:
            if loc.lat is not None and loc.lon is not None:
                geom = from_shape(Point(loc.lon, loc.lat), srid=4326)
            else:
                geom = None

            # Map ref_type to enum
            ref_type_map = {
                "snail": ReferenceType.snail,
                "grid": ReferenceType.grid,
                "coordinate": ReferenceType.coordinate,
                "relative": ReferenceType.terrain,
                "terrain": ReferenceType.terrain,
                "height": ReferenceType.terrain,
                "map_object": ReferenceType.terrain,
                "contact_target": ReferenceType.mixed,
            }
            ref_type = ref_type_map.get(loc.ref_type, ReferenceType.mixed)

            loc_ref = LocationReference(
                session_id=session_id,
                order_id=order.id,
                source_text=loc.source_text[:200],
                reference_type=ref_type,
                normalized_ref=loc.normalized_ref[:100],
                resolved_geometry=geom,
                resolution_depth=loc.resolution_depth,
                confidence=loc.confidence,
                validated=loc.lat is not None,
            )
            db.add(loc_ref)

        # ── Interpret intent ──────────────────────────────
        intent = await intent_interpreter.interpret(
            parsed=parsed,
            target_units=matched_units,
        )
        if intent:
            result.intent = intent
            order.parsed_intent = intent.model_dump(mode="json", exclude_none=True)

        # ── Build engine task ─────────────────────────────
        task = self._build_engine_task(
            order,
            parsed,
            resolved,
            intent,
            grid_service=grid_service,
            matched_units=matched_units,
            units_context=units_context,
            map_objects=map_objects,
        )
        result.engine_task = task

        coordination_units = [
            u for u in self._match_units(
                list(getattr(parsed, "coordination_unit_refs", []) or []),
                units_context,
                issuer_side,
            )
            if u.get("id") not in {m.get("id") for m in matched_units}
            and not u.get("is_destroyed")
            and u.get("comms_status") != "offline"
        ]

        if task:
            if task.get("type") == "resupply" and parsed.support_target_ref:
                supply_targets = [
                    u for u in self._match_units([parsed.support_target_ref], units_context, issuer_side)
                    if u.get("id") not in {m.get("id") for m in matched_units}
                ]
                if supply_targets:
                    supply_target = supply_targets[0]
                    task["support_target_unit_id"] = supply_target["id"]
                    task["follow_unit_id"] = supply_target["id"]
                    task["follow_unit_name"] = supply_target.get("name", "")
                    task["follow_distance_m"] = 60.0
                    if supply_target.get("lat") is not None and supply_target.get("lon") is not None:
                        task["target_location"] = {
                            "lat": supply_target["lat"],
                            "lon": supply_target["lon"],
                        }

            if best_contact_uid and task.get("type") in ("attack", "engage", "fire", "request_fire"):
                task["target_unit_id"] = best_contact_uid

            if coordination_units:
                coord_ids = [u["id"] for u in coordination_units]
                task["coordination_unit_ids"] = coord_ids

                arty_support_ids = [
                    u["id"] for u in coordination_units
                    if any(tok in (u.get("unit_type") or "") for tok in ("mortar", "artillery"))
                ]
                if arty_support_ids:
                    task["supporting_unit_ids"] = arty_support_ids

            if task.get("maneuver_kind") == "follow" and coordination_units:
                lead = coordination_units[0]
                task["follow_unit_id"] = lead["id"]
                task["follow_unit_name"] = lead.get("name", "")
                task["follow_distance_m"] = 120.0

                # Set initial target_location from leader's current position
                # so the unit starts moving immediately (tick engine will update dynamically)
                if lead.get("lat") is not None and lead.get("lon") is not None:
                    if not task.get("target_location"):
                        task["target_location"] = {
                            "lat": lead["lat"],
                            "lon": lead["lon"],
                        }

                side_hint = task.get("maneuver_side")
                if side_hint == "left":
                    task["follow_offset_m"] = {"rear": 120.0, "lateral": -40.0}
                elif side_hint == "right":
                    task["follow_offset_m"] = {"rear": 120.0, "lateral": 40.0}

            if (
                task.get("maneuver_kind") == "flank"
                and unit_pos
                and task.get("target_location")
            ):
                flank_side = task.get("maneuver_side")
                flank_point = self._compute_flank_approach_point(
                    unit_pos[0], unit_pos[1],
                    task["target_location"]["lat"], task["target_location"]["lon"],
                    side=flank_side,
                )
                if flank_point:
                    final_target = dict(task["target_location"])
                    task["flank_assault_location"] = final_target
                    task["flank_phase"] = "approach"
                    task["target_location"] = {"lat": flank_point[0], "lon": flank_point[1]}
                    task.pop("waypoints", None)
                    task["path_calc_tick"] = -999
                    task["combat_role"] = "flank"
                    task["combat_role_locked"] = True

        if task:
            order.parsed_order = {
                **order.parsed_order,
                **task,  # merge task fields into parsed_order for tick engine
            }

        # ── Grid boundary check: reject targets outside operations area ──
        target_outside_grid = False
        if task and grid_service and task.get("target_location"):
            tgt = task["target_location"]
            tgt_lat = tgt.get("lat")
            tgt_lon = tgt.get("lon")
            if tgt_lat is not None and tgt_lon is not None:
                if not grid_service.is_point_inside_grid(tgt_lat, tgt_lon):
                    target_outside_grid = True
                    # Clear the task — don't execute it
                    task = None
                    result.engine_task = None
                    order.status = OrderStatus.failed
                    order.parsed_intent = {
                        **(order.parsed_intent or {}),
                        "error": "target_outside_grid",
                    }

        # ── Immediate pathfinding: compute waypoints before tick ──
        # This gives the frontend an optimized trajectory line immediately
        # instead of showing a straight line until the next tick processes.
        route_impassable = False
        route_target_snail = ""
        MOVEABLE_TASK_TYPES = {
            "move", "attack", "advance", "disengage", "resupply",
            "breach", "lay_mines", "construct", "deploy_bridge",
        }
        if (
            task
            and not target_outside_grid
            and task.get("target_location")
            and task.get("type") in MOVEABLE_TASK_TYPES
        ):
            waypoints, path_ok = await self._compute_immediate_waypoints(
                task, matched_units, session_id, grid_service, db,
            )
            if path_ok and waypoints:
                task["waypoints"] = waypoints
                task["path_calc_tick"] = -1  # sentinel: pre-computed, recalc on first tick
                order.parsed_order = {**order.parsed_order, **task}
            elif not path_ok:
                route_impassable = True
                tgt = task.get("target_location", {})
                route_target_snail = task.get("target_snail", "")
                if not route_target_snail and grid_service:
                    try:
                        route_target_snail = grid_service.point_to_snail(
                            tgt.get("lat", 0), tgt.get("lon", 0), depth=2
                        ) or ""
                    except Exception:
                        pass
                # Clear the task — unit can't reach destination
                task = None
                result.engine_task = None
                order.status = OrderStatus.failed
                order.parsed_intent = {
                    **(order.parsed_intent or {}),
                    "error": "route_impassable",
                }

        # ── Fire range validation for artillery/mortar units ──
        # Check if fire-type orders target a location beyond the unit's weapon range.
        # If so, prepare range info for the unit response.
        fire_range_issues: dict[str, dict] = {}  # unit_id → range info dict
        if task and parsed.order_type and parsed.order_type.value == "fire" and task.get("target_location"):
            from backend.engine.combat import WEAPON_RANGE, ARTILLERY_TYPES
            target_loc = task["target_location"]
            tgt_lat = target_loc.get("lat")
            tgt_lon = target_loc.get("lon")
            if tgt_lat is not None and tgt_lon is not None:
                for unit_dict in matched_units:
                    if unit_dict.get("unit_type", "") not in ARTILLERY_TYPES:
                        continue
                    u_lat = unit_dict.get("lat")
                    u_lon = unit_dict.get("lon")
                    if u_lat is None or u_lon is None:
                        continue
                    weapon_range = WEAPON_RANGE.get(unit_dict["unit_type"], 5000)
                    # Check capabilities for extended range
                    caps = unit_dict.get("capabilities") or {}
                    if caps.get("mortar_range_m"):
                        weapon_range = max(weapon_range, caps["mortar_range_m"])
                    dist_to_target = self._haversine_m(u_lat, u_lon, tgt_lat, tgt_lon)
                    if dist_to_target > weapon_range:
                        # Compute bearing from unit to target
                        bearing_deg = self._bearing_deg(u_lat, u_lon, tgt_lat, tgt_lon)
                        compass = self._bearing_to_compass(bearing_deg)
                        # How far the unit needs to move to get in range
                        deficit_m = dist_to_target - weapon_range
                        fire_range_issues[unit_dict["id"]] = {
                            "dist_to_target_m": round(dist_to_target),
                            "weapon_range_m": weapon_range,
                            "deficit_m": round(deficit_m),
                            "bearing_deg": round(bearing_deg),
                            "compass": compass,
                        }

        # ── Pre-load shared data for unit situation building ──
        _preloaded_ctx = await self._preload_unit_situation_context(
            session_id, issuer_side, db,
        )

        # ── Check unit states & generate responses ────────
        all_ok = True
        for unit_dict in matched_units:
            resp_type, reason = response_generator.determine_response_type(parsed, unit_dict)

            if resp_type == ResponseType.no_response:
                all_ok = False
                continue
            elif resp_type == ResponseType.unable:
                all_ok = False

            # ── Override: artillery out-of-range fire mission ──
            range_info = fire_range_issues.get(unit_dict.get("id"))
            if range_info and resp_type in (ResponseType.wilco_fire, ResponseType.wilco, ResponseType.ack):
                resp_type = ResponseType.unable_range
                reason = "out_of_range"
                all_ok = False

            # ── Override: target outside grid (operations area) ──
            if target_outside_grid:
                resp_type = ResponseType.unable_area
                reason = "target_outside_area"
                all_ok = False

            # ── Override: route impassable (pathfinding failed) ──
            if route_impassable:
                resp_type = ResponseType.unable_route
                reason = "route_impassable"
                all_ok = False

            # Build situational awareness for status and command acknowledgments
            status_text = ""
            if resp_type == ResponseType.unable_area:
                # Target outside grid / operations area
                lang = parsed.language.value
                if lang == "ru":
                    status_text = (
                        "Не могу выполнить. Указанная цель находится за пределами района операции. "
                        "Запрашиваю уточнение координат."
                    )
                else:
                    status_text = (
                        "Cannot comply. Target location is outside the area of operations. "
                        "Requesting corrected coordinates."
                    )
            elif resp_type == ResponseType.unable_range and range_info:
                # Build range info text for the unit's response
                lang = parsed.language.value
                if lang == "ru":
                    compass_ru = {
                        "N": "С", "NNE": "ССВ", "NE": "СВ", "ENE": "ВСВ",
                        "E": "В", "ESE": "ВЮВ", "SE": "ЮВ", "SSE": "ЮЮВ",
                        "S": "Ю", "SSW": "ЮЮЗ", "SW": "ЮЗ", "WSW": "ЗЮЗ",
                        "W": "З", "WNW": "ЗСЗ", "NW": "СЗ", "NNW": "ССЗ",
                    }
                    compass_dir = compass_ru.get(range_info["compass"], range_info["compass"])
                    status_text = (
                        f"Дистанция до цели {range_info['dist_to_target_m']}м, "
                        f"макс. дальность {range_info['weapon_range_m']}м. "
                        f"Направление {compass_dir} ({range_info['bearing_deg']}°), "
                        f"необходимо выдвинуться минимум на {range_info['deficit_m']}м."
                    )
                else:
                    status_text = (
                        f"Distance to target {range_info['dist_to_target_m']}m, "
                        f"max range {range_info['weapon_range_m']}m. "
                        f"Bearing {range_info['compass']} ({range_info['bearing_deg']}°), "
                        f"need to advance at least {range_info['deficit_m']}m."
                    )
            elif resp_type == ResponseType.unable_route:
                lang = parsed.language.value
                ref = route_target_snail or ""
                if lang == "ru":
                    if ref:
                        status_text = (
                            f"Маршрут до {ref} непроходим. "
                            "Запрашиваю альтернативные координаты или инженерное обеспечение."
                        )
                    else:
                        status_text = (
                            "Маршрут до указанной позиции непроходим. "
                            "Запрашиваю альтернативные координаты или инженерное обеспечение."
                        )
                else:
                    if ref:
                        status_text = (
                            f"Route to {ref} impassable. "
                            "Requesting alternate coordinates or engineer support."
                        )
                    else:
                        status_text = (
                            "Route to designated position impassable. "
                            "Requesting alternate coordinates or engineer support."
                        )
            elif resp_type in (ResponseType.status, ResponseType.wilco, ResponseType.wilco_fire,
                               ResponseType.wilco_request_fire,
                               ResponseType.wilco_disengage, ResponseType.wilco_resupply,
                               ResponseType.wilco_observe, ResponseType.wilco_standby,
                               ResponseType.ack):
                situation = await self._build_unit_situation(
                    unit_dict, session_id, issuer_side, units_context,
                    db, grid_service, preloaded=_preloaded_ctx,
                )
                lang = parsed.language.value
                own_grid = situation.get("grid_ref", "")

                if resp_type == ResponseType.status:
                    # Full status report
                    status_text = response_generator.generate_status_report(
                        unit_dict, lang, situation=situation,
                    )
                elif resp_type == ResponseType.wilco_fire:
                    # Fire order: own position + target/readiness
                    target_snail = (task or {}).get("target_snail", "")
                    target_loc = (task or {}).get("target_location") or {}
                    salvos = (task or {}).get("salvos_remaining", 3)

                    # Resolve own_grid from unit position if missing
                    if not own_grid and unit_dict.get("lat") and unit_dict.get("lon") and grid_service:
                        try:
                            own_grid = grid_service.point_to_snail(
                                unit_dict["lat"], unit_dict["lon"], depth=2
                            ) or ""
                        except Exception:
                            pass

                    # Resolve target_snail from target_location if missing
                    if not target_snail and target_loc.get("lat") is not None and grid_service:
                        try:
                            target_snail = grid_service.point_to_snail(
                                target_loc["lat"], target_loc["lon"], depth=2
                            ) or ""
                        except Exception:
                            pass

                    pos_ru = f"нахожусь квадрат {own_grid}. " if own_grid else ""
                    pos_en = f"at grid {own_grid}. " if own_grid else ""
                    if target_snail:
                        if lang == "ru":
                            status_text = f"{pos_ru}готов открыть огонь по квадрату {target_snail}. {salvos} залпов."
                        else:
                            status_text = f"{pos_en}ready to fire on grid {target_snail}. {salvos} salvos."
                    elif target_loc.get("lat") is not None:
                        tgt_lat = round(target_loc["lat"], 4)
                        tgt_lon = round(target_loc["lon"], 4)
                        if lang == "ru":
                            status_text = f"{pos_ru}готов открыть огонь по {tgt_lat}, {tgt_lon}. {salvos} залпов."
                        else:
                            status_text = f"{pos_en}ready to fire on {tgt_lat}, {tgt_lon}. {salvos} salvos."
                    else:
                        # Generic fire support — no specific target yet
                        if lang == "ru":
                            status_text = f"{pos_ru}готов открыть огонь по противнику по мере обнаружения."
                        else:
                            status_text = f"{pos_en}ready to engage targets of opportunity."
                else:
                    # Brief situation for acknowledgments (position + key info)
                    order_type_val = parsed.order_type.value if parsed.order_type else ""
                    task_target_snail = (task or {}).get("target_snail", "")
                    task_target_loc = (task or {}).get("target_location")

                    # Resolve target_snail from target_location if missing
                    if not task_target_snail and task_target_loc and grid_service:
                        try:
                            t_lat = task_target_loc.get("lat")
                            t_lon = task_target_loc.get("lon")
                            if t_lat is not None and t_lon is not None:
                                task_target_snail = grid_service.point_to_snail(t_lat, t_lon, depth=2) or ""
                        except Exception:
                            pass

                    # Resolve own_grid from unit position if missing
                    if not own_grid and unit_dict.get("lat") and unit_dict.get("lon") and grid_service:
                        try:
                            own_grid = grid_service.point_to_snail(
                                unit_dict["lat"], unit_dict["lon"], depth=2
                            ) or ""
                        except Exception:
                            pass

                    # Include both current position AND destination for movement-related orders
                    _movement_types = ("move", "advance", "attack", "engage", "support",
                                       "flank", "assault", "withdraw", "retreat", "regroup")
                    # Defense/observe/halt orders — report position only
                    _static_types = ("defend", "observe", "halt", "regroup")
                    coord_refs = [r for r in ((task or {}).get("coordination_unit_refs") or []) if r]
                    coord_kind = (task or {}).get("coordination_kind")
                    maneuver_kind = (task or {}).get("maneuver_kind")
                    maneuver_side = (task or {}).get("maneuver_side")

                    if order_type_val == "request_fire" and task_target_snail:
                        coord_name = coord_refs[0] if coord_refs else "поддержку"
                        if lang == "ru":
                            prefix = f"нахожусь в квадрате {own_grid}. " if own_grid else ""
                            status_text = (
                                f"{prefix}передаю {coord_name} огневую задачу по квадрату {task_target_snail}"
                            )
                        else:
                            prefix = f"at grid {own_grid}. " if own_grid else ""
                            status_text = (
                                f"{prefix}passing {coord_name} a fire mission on grid {task_target_snail}"
                            )
                    elif order_type_val in _movement_types and task_target_snail and own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}. Выдвигаемся в квадрат {task_target_snail}"
                        else:
                            status_text = f"at grid {own_grid}. Moving to grid {task_target_snail}"
                    elif order_type_val in _movement_types and task_target_snail:
                        # Have destination but no own grid
                        if lang == "ru":
                            status_text = f"выдвигаемся в квадрат {task_target_snail}"
                        else:
                            status_text = f"moving to grid {task_target_snail}"
                    elif order_type_val == "defend" and own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}. Занимаю оборону"
                        else:
                            status_text = f"at grid {own_grid}. Holding position"
                    elif order_type_val == "observe" and own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}. Веду наблюдение"
                        else:
                            status_text = f"at grid {own_grid}. Observing"
                    elif order_type_val == "halt" and own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}. Стоим"
                        else:
                            status_text = f"at grid {own_grid}. Holding"
                    elif order_type_val == "disengage" and own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}. Разрываем контакт"
                        else:
                            status_text = f"at grid {own_grid}. Breaking contact"
                    elif own_grid:
                        if lang == "ru":
                            status_text = f"нахожусь в квадрате {own_grid}"
                        else:
                            status_text = f"at grid {own_grid}"
                    else:
                        status_text = response_generator.generate_brief_sitrep(
                            unit_dict, lang, situation=situation,
                        )

                    if maneuver_kind in {"flank", "bounding", "support_by_fire"}:
                        if lang == "ru":
                            if maneuver_kind == "flank":
                                maneuver_text = (
                                    "выхожу на левый фланг противника"
                                    if maneuver_side == "left"
                                    else "выхожу на правый фланг противника"
                                    if maneuver_side == "right"
                                    else "выхожу во фланг противника"
                                )
                            elif maneuver_kind == "bounding":
                                maneuver_text = "двигаюсь перебежками под прикрытием"
                            else:
                                maneuver_text = "занимаю позицию поддержки огнём"
                        else:
                            if maneuver_kind == "flank":
                                maneuver_text = (
                                    "maneuvering to the enemy left flank"
                                    if maneuver_side == "left"
                                    else "maneuvering to the enemy right flank"
                                    if maneuver_side == "right"
                                    else "maneuvering to the enemy flank"
                                )
                            elif maneuver_kind == "bounding":
                                maneuver_text = "advancing by bounds under cover"
                            else:
                                maneuver_text = "occupying a support-by-fire position"
                        status_text = f"{status_text}. {maneuver_text}" if status_text else maneuver_text

                    if coord_refs:
                        coord_name = coord_refs[0]
                        if lang == "ru":
                            if maneuver_kind == "follow":
                                coord_text = f"связываюсь с {coord_name}, следую за ним"
                            elif maneuver_kind == "bounding":
                                coord_text = f"связываюсь с {coord_name}, согласую движение перебежками"
                            elif maneuver_kind == "support_by_fire":
                                coord_text = f"связываюсь с {coord_name}, обеспечу поддержку огнём"
                            elif coord_kind == "covering_fire":
                                coord_text = (
                                    f"связываюсь с {coord_name}, выдвигаюсь под их огневым прикрытием"
                                )
                            elif coord_kind == "fire_support":
                                coord_text = (
                                    f"связываюсь с {coord_name}, согласую огневую поддержку"
                                )
                            else:
                                coord_text = f"связываюсь с {coord_name}, координирую действия"
                        else:
                            if maneuver_kind == "follow":
                                coord_text = f"linking up with {coord_name} and following it"
                            elif maneuver_kind == "bounding":
                                coord_text = f"linking up with {coord_name} for bounding movement"
                            elif maneuver_kind == "support_by_fire":
                                coord_text = f"linking up with {coord_name} to provide support by fire"
                            elif coord_kind == "covering_fire":
                                coord_text = f"linking up with {coord_name} and advancing under their covering fire"
                            elif coord_kind == "fire_support":
                                coord_text = f"linking up with {coord_name} for fire support"
                            else:
                                coord_text = f"linking up with {coord_name} to coordinate actions"
                        status_text = f"{status_text}. {coord_text}" if status_text else coord_text

            resp = response_generator.generate_response(
                parsed=parsed,
                unit=unit_dict,
                response_type=resp_type,
                reason_key=reason,
                status_text=status_text,
                support_target=getattr(parsed, "support_target_ref", "") or "",
            )
            if resp:
                result.responses.append(resp)

        await self._append_coordination_partner_responses(
            parsed=parsed,
            result=result,
            primary_units=matched_units,
            units_context=units_context,
            session_id=session_id,
            db=db,
            issuer_side=issuer_side,
            grid_service=grid_service,
            task=task,
        )

        # ── Set order status ──────────────────────────────
        if fire_range_issues and len(fire_range_issues) == len(matched_units):
            # ALL matched units out of range — auto-reposition + fire
            # Build a compound move-to-range → fire phased task
            from backend.engine.combat import DEFAULT_FIRE_SALVOS
            first_uid = list(fire_range_issues.keys())[0]
            range_info = fire_range_issues[first_uid]
            first_unit = next(u for u in matched_units if u["id"] == first_uid)

            # Compute advance position — move deficit + 100m safety margin
            advance_dist = range_info["deficit_m"] + 100
            u_lat, u_lon = first_unit["lat"], first_unit["lon"]
            bearing = range_info["bearing_deg"]
            adv_lat, adv_lon = self._destination_point(u_lat, u_lon, bearing, advance_dist)

            # Resolve advance point to snail path if possible
            adv_snail = None
            if grid_service:
                try:
                    adv_snail = grid_service.point_to_snail(adv_lat, adv_lon, depth=2)
                except Exception:
                    pass

            move_task = {
                "type": "move",
                "target_location": {"lat": adv_lat, "lon": adv_lon},
                "speed": "fast",
                "order_id": str(order.id),
                "advance_to_fire": True,
            }
            if adv_snail:
                move_task["target_snail"] = adv_snail

            fire_task = {
                "type": "fire",
                "target_location": task["target_location"],
                "order_id": str(order.id),
                "salvos_remaining": task.get("salvos_remaining", DEFAULT_FIRE_SALVOS),
            }
            if task.get("target_snail"):
                fire_task["target_snail"] = task["target_snail"]

            phases = [
                move_task,
                {
                    "condition": {"type": "task_completed"},
                    "task": fire_task,
                },
            ]

            order.parsed_order = {
                **(order.parsed_order or {}),
                "type": "move",
                "order_type": "move",
                "target_location": {"lat": adv_lat, "lon": adv_lon},
                "speed": "fast",
                "advance_to_fire": True,
                "phases": phases,
                **({"target_snail": adv_snail} if adv_snail else {}),
            }
            order.parsed_intent = {
                **(order.parsed_intent or {}),
                "advance_to_fire": True,
                "range_details": fire_range_issues,
            }
            order.status = OrderStatus.validated
            order.validated_at = datetime.now(timezone.utc)
        elif all_ok and task:
            order.status = OrderStatus.validated
            order.validated_at = datetime.now(timezone.utc)
        elif task:
            # Some units can't comply but order is still valid
            order.status = OrderStatus.validated
            order.validated_at = datetime.now(timezone.utc)
        else:
            order.status = OrderStatus.failed

    async def _generate_brief_ack_responses(
        self,
        order: Order,
        parsed: ParsedOrderData,
        result: OrderParseResult,
        units_context: list[dict],
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        grid_service: Any = None,
    ):
        """
        Generate brief unit acknowledgment responses for non-command messages
        (acknowledgments, status reports, encouragement). Units reply with a
        short "Принял" / "Acknowledged" so the commander knows they heard.
        """
        matched = []

        # Prefer explicit recipients from the order payload/UI when present.
        if order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched.append(u)
        if not matched:
            matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        # If still no units, pick first available same-side unit
        if not matched:
            same_side = [
                u for u in units_context
                if u.get("side") == issuer_side
                and not u.get("is_destroyed")
                and u.get("comms_status") != "offline"
            ]
            if same_side:
                matched = [same_side[0]]

        for unit_dict in matched:
            resp_type, reason = response_generator.determine_response_type(parsed, unit_dict)
            if resp_type == ResponseType.no_response:
                continue

            # For ack/report messages from commander, unit just acknowledges briefly
            resp = response_generator.generate_response(
                parsed=parsed,
                unit=unit_dict,
                response_type=ResponseType.ack,
                status_text="",
            )
            if resp:
                result.responses.append(resp)

    async def _process_status_request(
        self,
        order: Order,
        parsed: ParsedOrderData,
        result: OrderParseResult,
        units_context: list[dict],
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        grid_service: Any = None,
    ):
        """Process a status request — generate status reports from units."""
        matched = []

        # Prefer explicit recipients from the order payload/UI when present.
        if order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched.append(u)
        if not matched:
            matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        # Only fall back to all own-side units if BOTH text refs AND target_unit_ids are empty
        if not matched:
            matched = [u for u in units_context
                       if u.get("side") == issuer_side and not u.get("is_destroyed")]

        # Pre-load shared context once, then build per-unit situations
        _preloaded_sr = await self._preload_unit_situation_context(
            session_id, issuer_side, db,
        )

        if matched:
            _sit_map = {}
            for ud in matched:
                uid = ud.get("id", "")
                sit = await self._build_unit_situation(
                    ud, session_id, issuer_side, units_context, db, grid_service,
                    preloaded=_preloaded_sr,
                )
                _sit_map[uid] = sit
        else:
            _sit_map = {}

        request_focus = parsed.status_request_focus or self._infer_status_request_focus(
            order.original_text or ""
        )

        for unit_dict in matched:
            situation = _sit_map.get(unit_dict.get("id", ""), {})
            status_text = response_generator.generate_status_report(
                unit_dict,
                parsed.language.value,
                situation=situation,
                request_focus=request_focus,
            )
            resp = response_generator.generate_response(
                parsed=parsed,
                unit=unit_dict,
                response_type=ResponseType.status,
                status_text=status_text,
            )
            if resp:
                result.responses.append(resp)

        order.status = OrderStatus.completed
        order.completed_at = datetime.now(timezone.utc)

    @staticmethod
    def _infer_status_request_focus(original_text: str) -> list[str]:
        """Infer the requested info type for status questions when parser output is generic."""
        text_lower = (original_text or "").lower()
        focus_patterns = {
            "nearby_friendlies": [
                "кто рядом", "какие подразделения рядом", "какие части рядом",
                "какие силы рядом", "свои рядом", "friendly nearby", "friendlies nearby",
                "who is near you", "who's near you", "units near you", "nearby units",
            ],
            "terrain": [
                "местност", "опиши местност", "terrain", "ground around you",
                "describe terrain", "what terrain", "какая местность", "что за местность",
                "cover around you", "укрыти", "ground nearby",
            ],
            "enemy": [
                "противник", "enemy", "contact", "контакт", "кого видишь",
                "видишь противника", "enemy seen", "any enemy", "spot anything",
            ],
            "position": [
                "где ты", "где вы", "твоя позиция", "ваша позиция", "position",
                "where are you", "your location", "your grid", "координаты",
            ],
            "task": [
                "какая задача", "что делаешь", "что делаете", "чем занят",
                "current task", "what are you doing", "mission now", "orders now",
            ],
            "condition": [
                "состояни", "потери", "боеприпас", "бк", "мораль", "готовност",
                "condition", "readiness", "casualties", "ammo", "morale", "combat ready",
            ],
            "weather": [
                "погода", "видимость", "условия", "weather", "visibility", "conditions",
            ],
            "objects": [
                "объект", "препятств", "загражден", "строени", "map object",
                "obstacle", "structure", "bridge nearby",
            ],
            "road_distance": [
                "дорог", "road", "distance to road", "nearest road",
                "сколько до дороги", "дистанция до дороги", "как далеко до дороги",
            ],
        }

        focus = [
            name for name, patterns in focus_patterns.items()
            if any(pattern in text_lower for pattern in patterns)
        ]
        return focus or ["full"]

    @staticmethod
    def _offset_point(lat: float, lon: float, north_m: float = 0.0, east_m: float = 0.0) -> tuple[float, float]:
        return (
            lat + north_m / _M_PER_DEG_LAT,
            lon + east_m / _M_PER_DEG_LON_48,
        )

    def _default_engine_worksite(
        self,
        lat: float,
        lon: float,
        task_type: str,
        object_type: str | None = None,
    ) -> dict:
        object_type = object_type or ""
        if task_type == "lay_mines":
            nw = self._offset_point(lat, lon, north_m=45, east_m=-45)
            ne = self._offset_point(lat, lon, north_m=45, east_m=45)
            se = self._offset_point(lat, lon, north_m=-45, east_m=45)
            sw = self._offset_point(lat, lon, north_m=-45, east_m=-45)
            poly = Polygon([
                (nw[1], nw[0]),
                (ne[1], ne[0]),
                (se[1], se[0]),
                (sw[1], sw[0]),
            ])
            return poly.__geo_interface__

        if object_type in {"roadblock", "bridge_structure", "command_post_structure", "field_hospital", "supply_cache", "pillbox", "observation_tower"}:
            return Point(lon, lat).__geo_interface__

        line = LineString([
            (self._offset_point(lat, lon, east_m=-40)[1], self._offset_point(lat, lon, east_m=-40)[0]),
            (self._offset_point(lat, lon, east_m=40)[1], self._offset_point(lat, lon, east_m=40)[0]),
        ])
        return line.__geo_interface__

    def _resolve_breach_object(
        self,
        map_objects: list[Any] | None,
        unit_anchor: tuple[float, float] | None,
        target_anchor: tuple[float, float] | None,
        object_type: str | None,
    ) -> tuple[str | None, dict | None]:
        if not map_objects:
            return None, None

        preferred_types = []
        if object_type:
            preferred_types.append(object_type)
        preferred_types.extend([
            "minefield", "at_minefield", "barbed_wire", "concertina_wire",
            "roadblock", "anti_tank_ditch", "dragons_teeth",
        ])
        preferred_types = list(dict.fromkeys(preferred_types))

        anchor = target_anchor or unit_anchor
        if anchor is None:
            return None, None

        best_obj = None
        best_dist = float("inf")
        for obj in map_objects:
            if not getattr(obj, "is_active", True):
                continue
            obj_type = getattr(obj, "object_type", None)
            if preferred_types and obj_type not in preferred_types:
                continue
            geometry = getattr(obj, "geometry", None)
            if geometry is None:
                continue
            try:
                shp = to_shape(geometry)
                centroid = shp.centroid
                obj_lat, obj_lon = centroid.y, centroid.x
            except Exception:
                continue
            dlat = (obj_lat - anchor[0]) * _M_PER_DEG_LAT
            dlon = (obj_lon - anchor[1]) * _M_PER_DEG_LON_48
            dist = math.sqrt(dlat * dlat + dlon * dlon)
            if dist < best_dist:
                best_dist = dist
                best_obj = obj

        if best_obj is None:
            return None, None

        try:
            shp = to_shape(best_obj.geometry)
            centroid = shp.centroid
            target = {"lat": centroid.y, "lon": centroid.x}
        except Exception:
            target = None
        return str(best_obj.id), target

    def _build_engine_task(
        self,
        order: Order,
        parsed: ParsedOrderData,
        resolved_locations: list[ResolvedLocation],
        intent: Any,
        grid_service: Any = None,
        matched_units: list[dict] | None = None,
        units_context: list[dict] | None = None,
        map_objects: list[Any] | None = None,
    ) -> dict | None:
        """
        Build a task dict compatible with the tick engine's _order_to_task().

        The tick engine expects:
        {
            "type": "move"/"attack"/"defend"/etc.,
            "target_location": {"lat": float, "lon": float},
            "target_snail": "B8-2-4" (optional),
            "speed": "slow"/"fast" (optional),
            "order_id": str,
        }
        """
        if not parsed.order_type:
            return None

        task: dict = {
            "type": parsed.order_type.value,
            "order_id": str(order.id),
        }
        matched_units = matched_units or []
        units_context = units_context or []
        primary_unit = matched_units[0] if matched_units else None

        # Add speed if specified
        if parsed.speed:
            task["speed"] = parsed.speed.value

        # Add formation if specified
        if parsed.formation:
            task["formation"] = parsed.formation
        elif intent and hasattr(intent, "suggested_formation") and intent.suggested_formation:
            # Apply doctrinally suggested formation when none was explicitly ordered
            task["formation"] = intent.suggested_formation

        # Add target location from resolved locations
        for loc in resolved_locations:
            if loc.lat is not None and loc.lon is not None:
                task["target_location"] = {"lat": loc.lat, "lon": loc.lon}
                # Add snail reference if available from snail/grid resolution
                if loc.ref_type in ("snail", "grid"):
                    task["target_snail"] = loc.normalized_ref
                elif grid_service:
                    # For coordinate-based locations, try to resolve to snail path
                    try:
                        snail = grid_service.point_to_snail(loc.lat, loc.lon, depth=2)
                        if snail:
                            task["target_snail"] = snail
                    except Exception:
                        pass
                break  # Use first resolved location

        # Add engagement rules if specified
        if parsed.engagement_rules:
            task["engagement_rules"] = parsed.engagement_rules
        if parsed.purpose:
            task["purpose"] = parsed.purpose
        if parsed.support_target_ref:
            task["support_target_ref"] = parsed.support_target_ref
        if getattr(parsed, "merge_target_ref", None):
            task["merge_target_ref"] = parsed.merge_target_ref
        if getattr(parsed, "split_ratio", None) is not None:
            task["split_ratio"] = parsed.split_ratio
        if getattr(parsed, "map_object_type", None):
            task["map_object_type"] = parsed.map_object_type
            if (
                parsed.map_object_type == "smoke"
                and parsed.order_type.value in ("fire", "request_fire")
            ):
                task["fire_effect_type"] = "smoke"
                task["smoke_duration_ticks"] = 3
                task["salvos_remaining"] = 1
        if getattr(parsed, "coordination_unit_refs", None):
            task["coordination_unit_refs"] = [r for r in parsed.coordination_unit_refs if r]
        if getattr(parsed, "coordination_kind", None):
            task["coordination_kind"] = parsed.coordination_kind
        elif parsed.order_type.value == "request_fire":
            task["coordination_kind"] = "fire_support"
        if getattr(parsed, "maneuver_kind", None):
            task["maneuver_kind"] = parsed.maneuver_kind
        if getattr(parsed, "maneuver_side", None):
            task["maneuver_side"] = parsed.maneuver_side

        if parsed.order_type.value == "breach":
            unit_anchor = None
            if primary_unit and primary_unit.get("lat") is not None and primary_unit.get("lon") is not None:
                unit_anchor = (primary_unit["lat"], primary_unit["lon"])
            target_anchor = None
            if task.get("target_location"):
                target_anchor = (
                    task["target_location"].get("lat"),
                    task["target_location"].get("lon"),
                )
            target_object_id, breach_target = self._resolve_breach_object(
                map_objects,
                unit_anchor,
                target_anchor,
                getattr(parsed, "map_object_type", None),
            )
            if target_object_id:
                task["target_object_id"] = target_object_id
            if breach_target:
                task["target_location"] = breach_target

        if parsed.order_type.value in ("lay_mines", "construct", "deploy_bridge"):
            if "target_location" not in task and primary_unit and primary_unit.get("lat") is not None and primary_unit.get("lon") is not None:
                task["target_location"] = {
                    "lat": primary_unit["lat"],
                    "lon": primary_unit["lon"],
                }
            if parsed.order_type.value == "deploy_bridge":
                task["build_progress"] = task.get("build_progress", 0.0)
            elif task.get("target_location"):
                tgt = task["target_location"]
                object_type = getattr(parsed, "map_object_type", None)
                if parsed.order_type.value == "lay_mines":
                    task["mine_type"] = "at_minefield" if object_type == "at_minefield" else "minefield"
                if parsed.order_type.value == "construct":
                    task["object_type"] = object_type or "entrenchment"
                task["geometry"] = self._default_engine_worksite(
                    tgt["lat"],
                    tgt["lon"],
                    parsed.order_type.value,
                    object_type=(task.get("object_type") or object_type),
                )
                task["build_progress"] = task.get("build_progress", 0.0)

        # For commands that don't need a location (halt, regroup, report_status, disengage, withdraw, resupply)
        # Resupply target is auto-resolved by the resupply engine to the nearest supply source
        # request_fire target is resolved from nearest enemy contact at tick time
        if parsed.order_type.value in ("halt", "regroup", "report_status", "disengage", "withdraw", "resupply", "request_fire", "split", "merge"):
            return task

        # Commands that need a location but don't have one — still valid for
        # defend (defend current position) and observe (observe from current)
        if "target_location" not in task:
            if parsed.order_type.value in ("defend", "observe"):
                return task
            # Attack with fire_at_will (engage targets of opportunity) — no location needed
            if parsed.order_type.value in ("attack", "engage") and task.get("engagement_rules") == "fire_at_will":
                return task
            if parsed.order_type.value == "breach" and task.get("target_object_id"):
                return task
            # For move/attack without location, intent might provide it
            if intent and hasattr(intent, "action"):
                task["intent_action"] = intent.action
            return task  # Return task anyway — engine will handle as best it can

        return task

    @staticmethod
    def _compute_flank_approach_point(
        start_lat: float,
        start_lon: float,
        target_lat: float,
        target_lon: float,
        *,
        side: str | None = None,
        offset_deg: float = 60.0,
    ) -> tuple[float, float] | None:
        """Compute a doctrinal flank approach point offset from the target."""
        dy = (target_lat - start_lat) * _M_PER_DEG_LAT
        dx = (target_lon - start_lon) * _M_PER_DEG_LON_48
        dist = math.sqrt(dx * dx + dy * dy)
        if dist < 25:
            return None

        bearing = math.atan2(dx, dy)
        offset = math.radians(offset_deg)

        if side == "left":
            signs = (-1,)
        elif side == "right":
            signs = (1,)
        else:
            signs = (-1, 1)

        flank_dist_m = max(220.0, min(dist * 0.65, 550.0))
        best: tuple[float, float] | None = None
        for sign in signs:
            flank_bearing = bearing + sign * offset
            cand_lat = target_lat + flank_dist_m * math.cos(flank_bearing) / _M_PER_DEG_LAT
            cand_lon = target_lon + flank_dist_m * math.sin(flank_bearing) / _M_PER_DEG_LON_48
            if best is None:
                best = (cand_lat, cand_lon)
        return best

    @staticmethod
    def _find_nearest_contact_target(
        unit_lat: float,
        unit_lon: float,
        contacts: list[Any],
    ) -> dict[str, Any] | None:
        """Pick the nearest known contact for context-driven attack/fire orders."""
        best: dict[str, Any] | None = None
        best_dist = float("inf")

        for contact in contacts:
            geom = getattr(contact, "location_estimate", None)
            if geom is None:
                continue
            try:
                if hasattr(geom, "x") and hasattr(geom, "y"):
                    c_lon, c_lat = geom.x, geom.y
                else:
                    cpt = to_shape(geom)
                    c_lat, c_lon = cpt.y, cpt.x
            except Exception:
                continue

            dlat = (c_lat - unit_lat) * _M_PER_DEG_LAT
            dlon = (c_lon - unit_lon) * _M_PER_DEG_LON_48
            dist = math.sqrt(dlat ** 2 + dlon ** 2)
            if dist < best_dist:
                best_dist = dist
                best = {
                    "lat": c_lat,
                    "lon": c_lon,
                    "distance_m": dist,
                    "target_unit_id": str(contact.target_unit_id) if getattr(contact, "target_unit_id", None) else None,
                }

        return best

    async def _append_coordination_partner_responses(
        self,
        parsed: ParsedOrderData,
        result: OrderParseResult,
        primary_units: list[dict],
        units_context: list[dict],
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        grid_service: Any = None,
        task: dict | None = None,
    ) -> None:
        """
        Generate replies from contacted coordination/support units.

        Field manual basis:
        - Fire and maneuver: one element suppresses while another moves.
        - Fire support units acknowledge standby/support and keep range.
        - Recon units acknowledge observation/reporting rather than assault.
        """
        coord_refs = list(getattr(parsed, "coordination_unit_refs", []) or [])
        if not coord_refs or not primary_units:
            return

        primary_ids = {u.get("id") for u in primary_units}
        coordination_units = [
            u for u in self._match_units(coord_refs, units_context, issuer_side)
            if u.get("id") not in primary_ids
            and not u.get("is_destroyed")
            and u.get("comms_status") != "offline"
        ]
        if not coordination_units:
            return

        supported_unit = primary_units[0]
        supported_name = supported_unit.get("name", "supported unit")
        target_grid = (task or {}).get("target_snail", "") or ""
        if not target_grid and task and task.get("target_location") and grid_service:
            try:
                tgt = task["target_location"]
                target_grid = grid_service.point_to_snail(
                    tgt.get("lat"), tgt.get("lon"), depth=2
                ) or ""
            except Exception:
                pass

        for partner in coordination_units:
            partner_situation = await self._build_unit_situation(
                partner, session_id, issuer_side, units_context, db, grid_service,
            )
            own_grid = partner_situation.get("grid_ref", "") or ""
            resp_type, status_text = response_generator.generate_coordination_ack(
                unit=partner,
                language=parsed.language.value,
                supported_unit_name=supported_name,
                own_grid=own_grid,
                target_grid=target_grid,
                coordination_kind=getattr(parsed, "coordination_kind", None),
                explicit_fire_request=(
                    getattr(parsed, "order_type", None) is not None
                    and parsed.order_type.value == "request_fire"
                ),
            )
            resp = response_generator.generate_response(
                parsed=parsed,
                unit=partner,
                response_type=resp_type,
                status_text=status_text,
                support_target=supported_name,
            )
            if resp and not self._has_duplicate_response(result.responses, resp):
                result.responses.append(resp)

    @staticmethod
    def _has_duplicate_response(
        existing: list[UnitRadioResponse],
        candidate: UnitRadioResponse,
    ) -> bool:
        return any(
            r.from_unit_id == candidate.from_unit_id and r.text == candidate.text
            for r in existing
        )

    async def _process_reorganization_command(
        self,
        order: Order,
        parsed: ParsedOrderData,
        result: OrderParseResult,
        matched_units: list[dict],
        units_context: list[dict],
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
    ) -> bool:
        """Handle split/merge immediately as C2 reorganization commands."""
        order_type = parsed.order_type.value if parsed.order_type else ""
        if order_type not in {"split", "merge"}:
            return False

        from backend.api.units import (
            get_current_echelon,
            echelon_one_down,
            echelon_one_up,
            get_principal_type,
            make_unit_type,
            update_sidc_echelon,
            get_unit_latlon,
            haversine_m,
        )

        if order_type == "split":
            ratio = max(0.1, min(0.9, parsed.split_ratio or 0.5))
            split_units = []
            for unit_info in matched_units:
                db_unit = await db.get(Unit, uuid.UUID(unit_info["id"]))
                if db_unit is None or db_unit.is_destroyed:
                    continue

                base_name = db_unit.name
                prefix = base_name.rsplit("/", 1)[0] if "/" in base_name else base_name
                sibling_names = await db.execute(
                    select(Unit.name).where(
                        Unit.session_id == session_id,
                        Unit.is_destroyed == False,
                        Unit.name.like(f"{prefix}/%"),
                    )
                )
                existing_names = {name for (name,) in sibling_names}
                num = 1
                name_a = f"{prefix}/{num}"
                while name_a in existing_names:
                    num += 1
                    name_a = f"{prefix}/{num}"
                existing_names.add(name_a)
                num += 1
                name_b = f"{prefix}/{num}"
                while name_b in existing_names:
                    num += 1
                    name_b = f"{prefix}/{num}"

                pos_copy = db_unit.position
                if db_unit.position is not None:
                    try:
                        pt = to_shape(db_unit.position)
                        pos_copy = from_shape(Point(pt.x + 0.0004, pt.y), srid=4326)
                    except Exception:
                        pos_copy = db_unit.position

                new_echelon = echelon_one_down(get_current_echelon(db_unit.sidc))
                principal = get_principal_type(db_unit.unit_type)
                new_unit_type = make_unit_type(principal, new_echelon)
                new_sidc = update_sidc_echelon(db_unit.sidc, new_echelon)

                new_unit = Unit(
                    session_id=session_id,
                    side=db_unit.side,
                    name=name_b,
                    unit_type=new_unit_type,
                    sidc=new_sidc,
                    parent_unit_id=db_unit.parent_unit_id,
                    position=pos_copy,
                    heading_deg=db_unit.heading_deg,
                    strength=(db_unit.strength or 1.0) * ratio,
                    ammo=db_unit.ammo,
                    morale=db_unit.morale,
                    suppression=db_unit.suppression,
                    comms_status=db_unit.comms_status,
                    capabilities=dict(db_unit.capabilities) if db_unit.capabilities else None,
                    move_speed_mps=db_unit.move_speed_mps,
                    detection_range_m=db_unit.detection_range_m,
                    assigned_user_ids=list(db_unit.assigned_user_ids) if db_unit.assigned_user_ids else None,
                )
                db.add(new_unit)

                db_unit.name = name_a
                db_unit.strength = (db_unit.strength or 1.0) * (1 - ratio)
                db_unit.unit_type = new_unit_type
                db_unit.sidc = new_sidc
                split_units.append((db_unit, new_unit, ratio))

            await db.flush()
            for original, new_unit, used_ratio in split_units:
                if parsed.language.value == "ru":
                    text = (
                        f"Здесь {original.name}. Выполняю разделение. "
                        f"Новый элемент {new_unit.name}, доля {int(used_ratio * 100)}%. Приём."
                    )
                else:
                    text = (
                        f"{original.name} here. Splitting as ordered. "
                        f"New element {new_unit.name}, share {int(used_ratio * 100)} percent. Over."
                    )
                result.responses.append(UnitRadioResponse(
                    from_unit_name=original.name,
                    from_unit_id=str(original.id),
                    text=text,
                    language=parsed.language,
                    response_type=ResponseType.wilco,
                ))

            order.status = OrderStatus.completed
            order.completed_at = datetime.now(timezone.utc)
            order.parsed_intent = {
                **(order.parsed_intent or {}),
                "action": "split",
                "split_ratio": ratio,
            }
            return True

        merge_partner = None
        if parsed.merge_target_ref:
            partners = self._match_units([parsed.merge_target_ref], units_context, issuer_side)
            merge_partner = partners[0] if partners else None
        elif len(matched_units) >= 2:
            merge_partner = matched_units[1]

        if not matched_units or merge_partner is None:
            order.status = OrderStatus.failed
            result.responses.append(UnitRadioResponse(
                from_unit_name=matched_units[0]["name"] if matched_units else "Unit",
                from_unit_id=matched_units[0]["id"] if matched_units else None,
                text="Не могу выполнить слияние: не указан второй элемент." if parsed.language.value == "ru"
                else "Unable to merge: no partner element specified.",
                language=parsed.language,
                response_type=ResponseType.clarify,
            ))
            return True

        survivor = await db.get(Unit, uuid.UUID(matched_units[0]["id"]))
        absorbed = await db.get(Unit, uuid.UUID(merge_partner["id"]))
        if survivor is None or absorbed is None or survivor.is_destroyed or absorbed.is_destroyed:
            order.status = OrderStatus.failed
            return True

        if survivor.side != absorbed.side or get_principal_type(survivor.unit_type) != get_principal_type(absorbed.unit_type):
            order.status = OrderStatus.failed
            return True

        surv_lat, surv_lon = get_unit_latlon(survivor)
        abs_lat, abs_lon = get_unit_latlon(absorbed)
        if surv_lat is not None and abs_lat is not None:
            dist = haversine_m(surv_lat, surv_lon, abs_lat, abs_lon)
            if dist > 50:
                order.status = OrderStatus.failed
                result.responses.append(UnitRadioResponse(
                    from_unit_name=survivor.name,
                    from_unit_id=str(survivor.id),
                    text=f"Не могу выполнить слияние: дистанция до {absorbed.name} {dist:.0f}м." if parsed.language.value == "ru"
                    else f"Unable to merge: distance to {absorbed.name} is {dist:.0f}m.",
                    language=parsed.language,
                    response_type=ResponseType.unable,
                ))
                return True

        total_str = (survivor.strength or 0.0) + (absorbed.strength or 0.0)
        w_surv = (survivor.strength or 0.0) / total_str if total_str > 0 else 0.5
        w_abs = (absorbed.strength or 0.0) / total_str if total_str > 0 else 0.5
        survivor.strength = min(1.0, total_str)
        survivor.ammo = min(1.0, (survivor.ammo or 0.0) * w_surv + (absorbed.ammo or 0.0) * w_abs)
        survivor.morale = min(1.0, (survivor.morale or 0.0) * w_surv + (absorbed.morale or 0.0) * w_abs)
        survivor.suppression = max(0.0, (survivor.suppression or 0.0) * w_surv + (absorbed.suppression or 0.0) * w_abs)

        child_result = await db.execute(select(Unit).where(Unit.parent_unit_id == absorbed.id))
        for child in child_result.scalars().all():
            child.parent_unit_id = survivor.id

        absorbed.is_destroyed = True
        absorbed.strength = 0.0
        absorbed.current_task = None

        survivor.name = survivor.name.rsplit("/", 1)[0] if "/" in survivor.name else survivor.name
        new_echelon = echelon_one_up(get_current_echelon(survivor.sidc))
        principal = get_principal_type(survivor.unit_type)
        survivor.unit_type = make_unit_type(principal, new_echelon)
        survivor.sidc = update_sidc_echelon(survivor.sidc, new_echelon)
        await db.flush()

        result.responses.append(UnitRadioResponse(
            from_unit_name=survivor.name,
            from_unit_id=str(survivor.id),
            text=(
                f"Здесь {survivor.name}. Слился с {absorbed.name}, продолжаем одним элементом. Приём."
                if parsed.language.value == "ru"
                else f"{survivor.name} here. Merged with {absorbed.name}, continuing as one element. Over."
            ),
            language=parsed.language,
            response_type=ResponseType.wilco,
        ))
        order.status = OrderStatus.completed
        order.completed_at = datetime.now(timezone.utc)
        order.parsed_intent = {
            **(order.parsed_intent or {}),
            "action": "merge",
            "merge_target_ref": parsed.merge_target_ref or merge_partner.get("name"),
        }
        return True

    async def _compute_immediate_waypoints(
        self,
        task: dict,
        matched_units: list[dict],
        session_id: uuid.UUID,
        grid_service: Any,
        db: AsyncSession,
    ) -> tuple[list[dict] | None, bool]:
        """
        Compute A* pathfinding waypoints immediately after order confirmation.

        Returns:
            (waypoints_list, path_ok)
            - waypoints_list: list of {"lat", "lon"} dicts, or None
            - path_ok: True if path was found (or pathfinding not applicable),
                       False if route is impassable
        """
        if not grid_service or not matched_units:
            return None, True  # No grid → can't compute, not an error

        tgt = task.get("target_location")
        if not tgt:
            return None, True

        tgt_lat = tgt.get("lat")
        tgt_lon = tgt.get("lon")
        if tgt_lat is None or tgt_lon is None:
            return None, True

        # Use first matched unit's position as origin
        unit_dict = matched_units[0]
        u_lat = unit_dict.get("lat")
        u_lon = unit_dict.get("lon")
        if u_lat is None or u_lon is None:
            return None, True

        sid_str = str(session_id)

        try:
            import time as _pf_time
            _t0 = _pf_time.monotonic()

            # Fast path: only compute waypoints if graph is already in memory.
            # If not cached, let the tick engine compute on first tick (path_calc_tick=-1).
            from backend.services.pathfinding_service import get_cached_graph
            cached_graph = get_cached_graph(sid_str)
            if not cached_graph or not cached_graph.get("centroids"):
                logger.info("Pathfinding: graph not in memory cache, deferring to tick engine")
                return None, True

            # Load terrain data from cache or DB
            from backend.engine.terrain import get_cached_terrain_data
            cached = get_cached_terrain_data(sid_str)
            if cached:
                terrain_cells = cached.get("terrain_cells")
                elevation_cells = cached.get("elevation_cells")
                _terrain_src = "cache"
            else:
                from backend.models.terrain_cell import TerrainCell
                from backend.models.elevation_cell import ElevationCell
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
                _terrain_src = "db"

            _t_terrain = _pf_time.monotonic()

            if not terrain_cells:
                return None, True  # No terrain data → can't pathfind, not an error

            # Load static graph from memory → DB → build
            from backend.services.pathfinding_service import (
                PathfindingService,
            )

            # Graph is guaranteed in memory (checked at top of method)
            static_graph = cached_graph
            cell_centroids = cached_graph["centroids"]

            _t_graph = _pf_time.monotonic()


            # Build PathfindingService and find path
            speed_mode = task.get("speed", "slow")
            unit_side = unit_dict.get("side", "blue")

            service = PathfindingService(
                terrain_cells=terrain_cells,
                elevation_cells=elevation_cells,
                cell_centroids=cell_centroids,
                grid_service=grid_service,
                side=unit_side,
                speed_mode=speed_mode,
                static_graph=static_graph,
            )

            # Run CPU-bound A* in thread pool to avoid blocking the async event loop
            import asyncio as _aio
            import functools as _ft
            loop = _aio.get_running_loop()
            path = await loop.run_in_executor(
                None,
                _ft.partial(
                    service.find_path,
                    from_lat=u_lat,
                    from_lon=u_lon,
                    to_lat=tgt_lat,
                    to_lon=tgt_lon,
                ),
            )

            _t_path = _pf_time.monotonic()
            logger.info(
                "Immediate pathfinding timing: terrain=%s %.0fms, graph=%.0fms, A*=%.0fms, total=%.0fms",
                _terrain_src,
                (_t_terrain - _t0) * 1000,
                (_t_graph - _t_terrain) * 1000,
                (_t_path - _t_graph) * 1000,
                (_t_path - _t0) * 1000,
            )

            if path is None:
                # Route is truly impassable
                logger.info(
                    "Pathfinding: no route from (%.4f,%.4f) to (%.4f,%.4f) for %s",
                    u_lat, u_lon, tgt_lat, tgt_lon, unit_dict.get("name", "?"),
                )
                return None, False

            if len(path) <= 2:
                # Straight line is fine, no need for waypoints
                return None, True

            # Convert to waypoint dicts
            waypoints = [{"lat": p[0], "lon": p[1]} for p in path]
            return waypoints, True

        except Exception as e:
            logger.warning("Immediate pathfinding failed: %s", e)
            return None, True  # Fail gracefully → show straight line

    def _match_units(
        self,
        unit_refs: list[str],
        units_context: list[dict],
        issuer_side: str,
    ) -> list[dict]:
        """
        Fuzzy-match unit references from the LLM to actual unit records.

        Tries exact match, then substring, then Levenshtein-like scoring.
        Only matches units on the issuer's side.
        """
        if not unit_refs:
            return []

        same_side = [u for u in units_context if u.get("side") == issuer_side]
        matched = []
        matched_ids = set()

        for ref in unit_refs:
            ref_lower = ref.lower().strip()
            best_match = None
            best_score = 0

            for unit in same_side:
                if unit["id"] in matched_ids:
                    continue

                name_lower = unit.get("name", "").lower()
                unit_type = unit.get("unit_type", "").lower()

                # Exact name match
                if ref_lower == name_lower:
                    best_match = unit
                    best_score = 100
                    break

                # Check if ref is contained in name or vice versa
                score = 0
                if ref_lower in name_lower:
                    score = 80
                elif name_lower in ref_lower:
                    score = 70

                # Number matching (e.g., "первый" → "1st", "второй" → "2nd")
                ref_nums = self._extract_numbers(ref_lower)
                name_nums = self._extract_numbers(name_lower)
                if ref_nums and name_nums and ref_nums & name_nums:
                    score = max(score, 60)

                # Type keyword matching
                type_keywords = self._get_type_keywords(unit_type)
                for kw in type_keywords:
                    if kw in ref_lower:
                        score += 20  # was 15 — boost so artillery stem matches reach threshold

                # Russian inflected artillery/mortar stems — direct type match
                _arty_ru_stems = ["артилл", "батар", "артиллер"]
                _mort_ru_stems = ["мином", "миномёт", "миномет"]
                if any(s in ref_lower for s in _arty_ru_stems) and "artillery" in unit_type:
                    score = max(score, 60)
                if any(s in ref_lower for s in _mort_ru_stems) and "mortar" in unit_type:
                    score = max(score, 60)

                # Russian ordinals
                ru_ordinals = {
                    "первый": "1", "первая": "1", "первое": "1",
                    "второй": "2", "вторая": "2", "второе": "2",
                    "третий": "3", "третья": "3", "третье": "3",
                    "четвёртый": "4", "четвертый": "4",
                    "пятый": "5", "шестой": "6",
                }
                for ru, num in ru_ordinals.items():
                    if ru in ref_lower and num in name_lower:
                        score = max(score, 65)

                # "разведка" → recon
                if any(kw in ref_lower for kw in ["разведка", "развед", "recon"]):
                    if "recon" in unit_type:
                        score = max(score, 70)

                # "группа" → group/team
                if any(kw in ref_lower for kw in ["группа", "group", "team"]):
                    if any(t in unit_type for t in ["team", "section"]):
                        score = max(score, 50)

                if score > best_score:
                    best_score = score
                    best_match = unit

            if best_match and best_score >= 40:
                matched.append(best_match)
                matched_ids.add(best_match["id"])

        return matched

    def _extract_numbers(self, text: str) -> set[str]:
        """Extract numeric strings from text."""
        import re
        return set(re.findall(r'\d+', text))

    def _get_type_keywords(self, unit_type: str) -> list[str]:
        """Get searchable keywords for a unit type."""
        keywords = {
            "infantry": ["пехот", "infantry", "стрелк", "взвод", "platoon"],
            "tank": ["танк", "tank", "бронет"],
            "recon": ["развед", "recon", "наблюд"],
            "mortar": ["миномёт", "минометн", "mortar"],
            "artillery": ["артилл", "artillery", "батар"],
            "engineer": ["сапёр", "сапер", "инженер", "engineer"],
            "logistics": ["логист", "снабж", "supply", "logistics"],
            "air": ["авиац", "air", "aviation", "helicopter", "drone", "бпла"],
            "sniper": ["снайп", "sniper"],
        }
        result = []
        for key, kws in keywords.items():
            if key in unit_type:
                result.extend(kws)
        return result

    # ── Context loaders ──────────────────────────────────

    async def _load_units_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[dict]:
        """Load all units for context injection into LLM."""
        result = await db.execute(
            select(Unit).where(Unit.session_id == session_id)
        )
        units = result.scalars().all()

        context = []
        for u in units:
            pos_lat, pos_lon = None, None
            if u.position:
                try:
                    pt = to_shape(u.position)
                    pos_lat, pos_lon = pt.y, pt.x
                except Exception:
                    pass

            context.append({
                "id": str(u.id),
                "name": u.name,
                "unit_type": u.unit_type,
                "side": u.side.value if hasattr(u.side, 'value') else u.side,
                "is_destroyed": u.is_destroyed,
                "strength": u.strength or 1.0,
                "morale": u.morale or 1.0,
                "ammo": u.ammo or 1.0,
                "suppression": u.suppression or 0.0,
                "comms_status": u.comms_status.value if hasattr(u.comms_status, 'value') else (u.comms_status or "operational"),
                "current_task": u.current_task,
                "heading_deg": u.heading_deg,
                "lat": pos_lat,
                "lon": pos_lon,
                "parent_unit_id": str(u.parent_unit_id) if u.parent_unit_id else None,
                "capabilities": u.capabilities,
                "detection_range_m": u.detection_range_m,
            })
        return context

    async def _load_grid(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> tuple[dict | None, Any]:
        """Load grid definition and build GridService."""
        result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        gd = result.scalar_one_or_none()
        if gd is None:
            return None, None

        from backend.services.grid_service import GridService
        grid_service = GridService(gd)

        grid_info = {
            "columns": gd.columns,
            "rows": gd.rows,
            "labeling_scheme": gd.labeling_scheme or "alphanumeric",
            "base_square_size_m": gd.base_square_size_m,
        }
        return grid_info, grid_service

    async def _load_elevation_peaks(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> list[dict]:
        """Load elevation peaks using terrain.py's 3-tier cache (memory → DB → compute)."""
        try:
            from backend.api.terrain import get_elevation_peaks_cached
            return await get_elevation_peaks_cached(session_id, db)
        except Exception as e:
            logger.warning("Failed to load elevation peaks: %s", e)
            return []

    async def _load_map_objects_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str | None = None,
    ) -> list[dict]:
        """Load map objects with geometry for named location resolution (e.g. 'Airfield')."""
        try:
            from backend.models.map_object import MapObject, ObjectSide
            from geoalchemy2.shape import to_shape

            query = select(MapObject).where(
                MapObject.session_id == session_id,
                MapObject.is_active == True,
            )
            # Respect fog-of-war for object discovery when side is known.
            # Friendly + neutral objects are visible; enemy objects require discovery.
            if issuer_side in ("blue", "red"):
                discovered_filter = (
                    MapObject.discovered_by_blue == True
                    if issuer_side == "blue"
                    else MapObject.discovered_by_red == True
                )
                query = query.where(
                    or_(
                        MapObject.side == ObjectSide.neutral,
                        MapObject.side == ObjectSide(issuer_side),
                        discovered_filter,
                    )
                )

            result = await db.execute(query)
            objects = result.scalars().all()
            context = []
            for obj in objects:
                lat, lon = None, None
                if obj.geometry:
                    try:
                        shape = to_shape(obj.geometry)
                        centroid = shape.centroid
                        lon, lat = centroid.x, centroid.y
                    except Exception:
                        pass
                name = ""
                if obj.properties:
                    name = obj.properties.get("name", "") or obj.properties.get("label", "")
                context.append({
                    "id": str(obj.id),
                    "object_type": obj.object_type,
                    "object_category": (
                        obj.object_category.value
                        if hasattr(obj.object_category, "value")
                        else str(obj.object_category)
                    ),
                    "side": obj.side.value if hasattr(obj.side, "value") else str(obj.side),
                    "name": name,
                    "label": obj.label,
                    "lat": lat,
                    "lon": lon,
                })
            return context
        except Exception as e:
            logger.warning("Failed to load map objects context: %s", e)
            return []

    async def _get_game_time(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> str:
        """Get current game time as a string."""
        from backend.models.session import Session
        result = await db.execute(
            select(Session.current_time, Session.tick).where(Session.id == session_id)
        )
        row = result.first()
        if row and row[0]:
            return f"Turn {row[1]}, {row[0].isoformat()}"
        return "Unknown"

    async def _preload_unit_situation_context(
        self,
        session_id: uuid.UUID,
        issuer_side: str,
        db: AsyncSession,
    ) -> dict:
        """Pre-load all shared data needed by _build_unit_situation in one pass."""
        import asyncio as _aio
        from backend.models.terrain_cell import TerrainCell
        from backend.models.elevation_cell import ElevationCell
        from backend.models.contact import Contact
        from backend.models.map_object import MapObject
        from backend.models.session import Session as Sess
        from backend.models.scenario import Scenario

        async def _load_terrain_cells():
            r = await db.execute(
                select(
                    TerrainCell.snail_path,
                    TerrainCell.terrain_type,
                    TerrainCell.modifiers,
                    TerrainCell.elevation_m,
                    TerrainCell.slope_deg,
                    TerrainCell.depth,
                ).where(TerrainCell.session_id == session_id)
            )
            return {row[0]: row for row in r.all()}

        async def _load_elevation_cells():
            r = await db.execute(
                select(
                    ElevationCell.snail_path,
                    ElevationCell.elevation_m,
                    ElevationCell.slope_deg,
                    ElevationCell.aspect_deg,
                ).where(ElevationCell.session_id == session_id)
            )
            return {row[0]: row for row in r.all()}

        async def _load_contacts():
            r = await db.execute(
                select(Contact).where(
                    Contact.session_id == session_id,
                    Contact.observing_side == issuer_side,
                    Contact.is_stale == False,
                ).limit(15)
            )
            return r.scalars().all()

        async def _load_map_objects():
            discovery_filter = (
                MapObject.discovered_by_blue == True if issuer_side == "blue"
                else MapObject.discovered_by_red == True
            )
            r = await db.execute(
                select(MapObject).where(
                    MapObject.session_id == session_id,
                    MapObject.is_active == True,
                    discovery_filter,
                )
            )
            return r.scalars().all()

        async def _load_session_scenario():
            r = await db.execute(select(Sess).where(Sess.id == session_id))
            sess = r.scalar_one_or_none()
            scen = None
            if sess:
                r2 = await db.execute(select(Scenario).where(Scenario.id == sess.scenario_id))
                scen = r2.scalar_one_or_none()
            return sess, scen

        async def _load_peaks():
            return await self._load_elevation_peaks(session_id, db)

        (
            terrain_cells,
            elevation_cells,
            contacts,
            map_objects,
            (session_obj, scenario_obj),
            peaks,
        ) = await _aio.gather(
            _load_terrain_cells(),
            _load_elevation_cells(),
            _load_contacts(),
            _load_map_objects(),
            _load_session_scenario(),
            _load_peaks(),
        )

        # Build road cells index from terrain_cells
        road_cells = [
            (row[4] if row[4] is not None else 0, row[3] if row[3] is not None else 0, row[0])
            for path, row in terrain_cells.items()
            if row[1] in ("road", "bridge") and row[3] is not None and row[4] is not None
        ]
        # Actually we need centroid_lat/lon which aren't in our select... use snail_path keys
        # We'll store full row data keyed by snail_path for road lookup

        current_tick = session_obj.tick if session_obj else 0

        return {
            "terrain_cells": terrain_cells,
            "elevation_cells": elevation_cells,
            "contacts": contacts,
            "map_objects": map_objects,
            "session_obj": session_obj,
            "scenario_obj": scenario_obj,
            "peaks": peaks,
            "current_tick": current_tick,
        }

    async def _build_unit_situation(
        self,
        unit_dict: dict,
        session_id: uuid.UUID,
        issuer_side: str,
        all_units: list[dict],
        db: AsyncSession,
        grid_service: Any = None,
        preloaded: dict | None = None,
    ) -> dict:
        """
        Build a rich situational awareness context dict for a unit.

        Includes:
        - Map coordinates (lat/lon) AND snail grid reference
        - Terrain at unit's position + surrounding terrain analysis
        - Elevation, slope, height advantage info
        - Known enemy contacts visible to this side
        - Nearby friendly units (within 2km) with status summary
        - Parent unit and subordinate units
        - Recent events involving this unit (last 5 ticks)
        - Recent orders to this unit (last 5)
        - Weather / environment conditions
        - Current game time and time-of-day
        - Compass heading / facing direction
        - Nearby discovered map objects (minefields, obstacles, structures)
        - Combat status (under fire, suppressed, etc.)
        - Current task description with both snail + coordinate references

        This context is used for:
        - Richer status reports (unit reports what it sees)
        - LLM response generation (unit has full awareness)
        """
        import math
        situation = {}
        unit_lat = unit_dict.get("lat")
        unit_lon = unit_dict.get("lon")
        unit_id = unit_dict.get("id")

        # ── Map coordinates (lat/lon) ──────────────────────
        if unit_lat is not None and unit_lon is not None:
            situation["coordinates"] = {
                "lat": round(unit_lat, 6),
                "lon": round(unit_lon, 6),
            }

        # ── Grid reference ────────────────────────────────
        if grid_service and unit_lat and unit_lon:
            try:
                snail = grid_service.point_to_snail(unit_lat, unit_lon, depth=2)
                situation["grid_ref"] = snail
            except Exception:
                pass

        # ── Heading / compass direction ────────────────────
        heading = unit_dict.get("heading_deg")
        if heading is not None:
            situation["heading_deg"] = round(heading, 1)
            # Convert to compass direction
            compass_dirs = [
                (0, "N"), (22.5, "NNE"), (45, "NE"), (67.5, "ENE"),
                (90, "E"), (112.5, "ESE"), (135, "SE"), (157.5, "SSE"),
                (180, "S"), (202.5, "SSW"), (225, "SW"), (247.5, "WSW"),
                (270, "W"), (292.5, "WNW"), (315, "NW"), (337.5, "NNW"),
            ]
            normalized_heading = heading % 360
            closest = min(compass_dirs, key=lambda d: min(
                abs(normalized_heading - d[0]),
                360 - abs(normalized_heading - d[0])
            ))
            situation["heading_compass"] = closest[1]

        # ── Current task description with coordinates ──────
        task = unit_dict.get("current_task")
        if task:
            task_info = {"type": task.get("type", "unknown")}
            if task.get("target_snail"):
                task_info["target_snail"] = task["target_snail"]
            if task.get("target_location"):
                tl = task["target_location"]
                task_info["target_coordinates"] = {
                    "lat": round(tl.get("lat", 0), 6),
                    "lon": round(tl.get("lon", 0), 6),
                }
                # Compute distance to target and ETA
                if unit_lat and unit_lon:
                    try:
                        t_lat, t_lon = tl.get("lat", 0), tl.get("lon", 0)
                        dlat = math.radians(t_lat - unit_lat)
                        dlon = math.radians(t_lon - unit_lon)
                        a = (math.sin(dlat / 2) ** 2 +
                             math.cos(math.radians(unit_lat)) *
                             math.cos(math.radians(t_lat)) *
                             math.sin(dlon / 2) ** 2)
                        dist_to_target_m = 6371000 * 2 * math.atan2(
                            math.sqrt(a), math.sqrt(1 - a))
                        task_info["distance_to_target_m"] = round(dist_to_target_m)
                        # Estimate ETA in ticks (assuming 60s ticks)
                        base_speed = unit_dict.get("move_speed_mps", 5.0) if unit_dict.get("move_speed_mps") else 5.0
                        # Approximate with terrain factor 0.6
                        eff_speed = base_speed * 0.6
                        if eff_speed > 0 and dist_to_target_m > 0:
                            eta_seconds = dist_to_target_m / eff_speed
                            eta_ticks = round(eta_seconds / 60)
                            task_info["eta_ticks"] = max(1, eta_ticks)
                    except Exception:
                        pass
            if task.get("speed"):
                task_info["speed_mode"] = task["speed"]
            if task.get("purpose"):
                task_info["purpose"] = task["purpose"]
            if task.get("coordination_unit_refs"):
                task_info["coordination_unit_refs"] = list(task["coordination_unit_refs"])
            if task.get("coordination_kind"):
                task_info["coordination_kind"] = task["coordination_kind"]
            if task.get("support_target_ref"):
                task_info["support_target_ref"] = task["support_target_ref"]
            situation["current_task"] = task_info

        # ── Terrain at position ───────────────────────────
        if unit_lat and unit_lon:
            try:
                _tc_map = preloaded.get("terrain_cells", {}) if preloaded else {}
                _ec_map = preloaded.get("elevation_cells", {}) if preloaded else {}

                # Find terrain cell by snail path (in-memory lookup)
                if situation.get("grid_ref"):
                    snail_path = situation["grid_ref"]
                    tc_row = None
                    # Walk up the path hierarchy
                    sp = snail_path
                    while sp and not tc_row:
                        tc_row = _tc_map.get(sp)
                        if not tc_row and "-" in sp:
                            sp = sp.rsplit("-", 1)[0]
                        else:
                            break

                    # Fallback: infer from child cells in preloaded map
                    if not tc_row:
                        prefix = situation["grid_ref"] + "-"
                        child_rows = [v for k, v in _tc_map.items() if k.startswith(prefix)]
                        if child_rows:
                            terrain_counts: dict[str, int] = {}
                            elev_samples: list[float] = []
                            slope_samples: list[float] = []
                            for row in child_rows:
                                tt = row[1]
                                terrain_counts[tt] = terrain_counts.get(tt, 0) + 1
                                if row[3] is not None:
                                    elev_samples.append(float(row[3]))
                                if row[4] is not None:
                                    slope_samples.append(float(row[4]))
                            dominant = max(terrain_counts.items(), key=lambda item: (item[1], item[0]))[0]
                            terrain_info = {"type": dominant, "modifiers": {}}
                            if elev_samples:
                                terrain_info["elevation_m"] = round(sum(elev_samples) / len(elev_samples), 1)
                            if slope_samples:
                                terrain_info["slope_deg"] = round(sum(slope_samples) / len(slope_samples), 1)
                            terrain_info["inferred"] = True
                            situation["terrain"] = terrain_info

                    if tc_row and "terrain" not in situation:
                        # tc_row = (snail_path, terrain_type, modifiers, elevation_m, slope_deg, depth)
                        terrain_info = {
                            "type": tc_row[1],
                            "modifiers": tc_row[2] or {},
                        }
                        if tc_row[3] is not None:
                            terrain_info["elevation_m"] = round(float(tc_row[3]), 1)
                        if tc_row[4] is not None:
                            terrain_info["slope_deg"] = round(float(tc_row[4]), 1)
                        situation["terrain"] = terrain_info

                    # Elevation data from preloaded
                    ec_row = _ec_map.get(situation["grid_ref"])
                    if ec_row:
                        # ec_row = (snail_path, elevation_m, slope_deg, aspect_deg)
                        elev_info = {"elevation_m": round(float(ec_row[1]), 1)}
                        if ec_row[2] is not None:
                            elev_info["slope_deg"] = round(float(ec_row[2]), 1)
                        if ec_row[3] is not None:
                            elev_info["aspect_deg"] = round(float(ec_row[3]), 1)
                        situation["elevation"] = elev_info
                        if "terrain" in situation and "elevation_m" not in situation["terrain"]:
                            situation["terrain"]["elevation_m"] = elev_info["elevation_m"]
            except Exception:
                pass

        # ── Top-level shorthand keys for terrain/elevation ──
        if "terrain" in situation:
            situation["terrain_type"] = situation["terrain"].get("type", "open")
            if "elevation_m" in situation["terrain"]:
                situation["elevation_m"] = situation["terrain"]["elevation_m"]
        elif "elevation" in situation:
            situation["elevation_m"] = situation["elevation"].get("elevation_m")

        # ── Surrounding terrain (adjacent cells) ──────────
        if situation.get("grid_ref") and grid_service:
            try:
                _tc_map = preloaded.get("terrain_cells", {}) if preloaded else {}
                current_ref = situation["grid_ref"]
                depth = current_ref.count("-")
                if depth > 0:
                    parent_path = current_ref.rsplit("-", 1)[0]
                    prefix = parent_path + "-"
                    surrounding: dict[str, int] = {}
                    for k, v in _tc_map.items():
                        if k.startswith(prefix) and v[5] == depth:  # v[5] = depth
                            tt = v[1]
                            surrounding[tt] = surrounding.get(tt, 0) + 1
                    if surrounding:
                        situation["surrounding_terrain"] = surrounding
            except Exception:
                pass

        # ── Nearest road / route access ───────────────────
        # Skip expensive road search — minor context value vs cost

        # ── Nearby height tops (elevation peaks) ───────────
        if unit_lat and unit_lon:
            try:
                elevation_peaks = preloaded.get("peaks") if preloaded else None
                if not elevation_peaks:
                    elevation_peaks = await self._load_elevation_peaks(session_id, db)
                if elevation_peaks:
                    nearby_heights = []
                    for peak in elevation_peaks:
                        p_lat = peak.get("lat", 0)
                        p_lon = peak.get("lon", 0)
                        dlat = math.radians(p_lat - unit_lat)
                        dlon = math.radians(p_lon - unit_lon)
                        a = (math.sin(dlat / 2) ** 2 +
                             math.cos(math.radians(unit_lat)) *
                             math.cos(math.radians(p_lat)) *
                             math.sin(dlon / 2) ** 2)
                        dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                        if dist_m <= 5000:  # within 5km
                            nearby_heights.append({
                                "label": peak["label"],
                                "label_ru": peak["label_ru"],
                                "elevation_m": peak["elevation_m"],
                                "distance_m": round(dist_m),
                                "snail_path": peak.get("snail_path", ""),
                            })
                    nearby_heights.sort(key=lambda h: h["distance_m"])
                    if nearby_heights:
                        situation["nearby_heights"] = nearby_heights[:8]
            except Exception:
                pass

        # ── Weather / environment conditions ───────────────
        try:
            session_obj = preloaded.get("session_obj") if preloaded else None
            scenario_obj = preloaded.get("scenario_obj") if preloaded else None
            if not session_obj:
                from backend.models.session import Session
                sess_result = await db.execute(
                    select(Session).where(Session.id == session_id)
                )
                session_obj = sess_result.scalar_one_or_none()
            if session_obj:
                # Game time
                if session_obj.current_time:
                    game_time = session_obj.current_time
                    situation["game_time"] = {
                        "datetime": game_time.isoformat(),
                        "hour": game_time.hour,
                        "tick": session_obj.tick,
                    }
                    # Derive time-of-day period
                    hour = game_time.hour
                    if 6 <= hour < 12:
                        situation["game_time"]["period"] = "morning"
                    elif 12 <= hour < 17:
                        situation["game_time"]["period"] = "afternoon"
                    elif 17 <= hour < 21:
                        situation["game_time"]["period"] = "evening"
                    else:
                        situation["game_time"]["period"] = "night"

                # Environment / weather from scenario
                if not scenario_obj:
                    from backend.models.scenario import Scenario
                    scen_result = await db.execute(
                        select(Scenario).where(Scenario.id == session_obj.scenario_id)
                    )
                    scenario_obj = scen_result.scalar_one_or_none()
                if scenario_obj and scenario_obj.environment:
                    env = scenario_obj.environment
                    weather_info = {}
                    if "weather" in env:
                        weather_info["weather"] = env["weather"]
                    if "visibility" in env:
                        weather_info["visibility"] = env["visibility"]
                    if "wind" in env:
                        weather_info["wind"] = env["wind"]
                    if "temperature" in env:
                        weather_info["temperature"] = env["temperature"]
                    if "precipitation" in env:
                        weather_info["precipitation"] = env["precipitation"]
                    if "light_level" in env:
                        weather_info["light_level"] = env["light_level"]
                    # Fallback: include entire env dict if specific keys not present
                    if not weather_info and env:
                        weather_info = env
                    if weather_info:
                        situation["weather"] = weather_info
        except Exception:
            pass

        # ── Known enemy contacts (this side) ──────────────
        try:
            contacts = preloaded.get("contacts") if preloaded else None
            if contacts is None:
                from backend.models.contact import Contact
                contacts_result = await db.execute(
                    select(Contact).where(
                        Contact.session_id == session_id,
                        Contact.observing_side == issuer_side,
                        Contact.is_stale == False,
                    ).limit(15)
                )
                contacts = contacts_result.scalars().all()

            nearby_contacts = []
            for c in contacts:
                c_lat, c_lon = None, None
                if c.location_estimate:
                    try:
                        pt = to_shape(c.location_estimate)
                        c_lat, c_lon = pt.y, pt.x
                    except Exception:
                        pass

                dist_m = None
                if unit_lat and unit_lon and c_lat and c_lon:
                    # Haversine approximation
                    dlat = math.radians(c_lat - unit_lat)
                    dlon = math.radians(c_lon - unit_lon)
                    a = (math.sin(dlat / 2) ** 2 +
                         math.cos(math.radians(unit_lat)) *
                         math.cos(math.radians(c_lat)) *
                         math.sin(dlon / 2) ** 2)
                    dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                c_info = {
                    "type": c.estimated_type or "unknown",
                    "size": c.estimated_size,
                    "confidence": round(c.confidence, 2),
                    "source": c.source,
                    "stale": c.is_stale,
                }
                # Include both coordinate and grid reference for contacts
                if c_lat is not None and c_lon is not None:
                    c_info["coordinates"] = {
                        "lat": round(c_lat, 6),
                        "lon": round(c_lon, 6),
                    }
                if dist_m is not None:
                    c_info["distance_m"] = round(dist_m)
                    # Bearing from unit to contact
                    if unit_lat and unit_lon and c_lat and c_lon:
                        bearing_rad = math.atan2(
                            math.radians(c_lon - unit_lon) * math.cos(math.radians(c_lat)),
                            math.radians(c_lat - unit_lat)
                        )
                        bearing_deg = math.degrees(bearing_rad) % 360
                        c_info["bearing_deg"] = round(bearing_deg)
                    # Include grid ref for contact if possible
                    if grid_service and c_lat and c_lon:
                        try:
                            c_info["grid_ref"] = grid_service.point_to_snail(c_lat, c_lon, depth=2)
                        except Exception:
                            pass
                nearby_contacts.append(c_info)

            # Sort by distance, closest first
            nearby_contacts.sort(key=lambda x: x.get("distance_m", 999999))
            situation["contacts"] = nearby_contacts[:10]
        except Exception:
            pass

        # ── Nearby friendly units (within ~2km) ───────────
        if unit_lat and unit_lon:
            nearby_friendlies = []
            for u in all_units:
                if u.get("id") == unit_id:
                    continue
                if u.get("side") != issuer_side:
                    continue
                if u.get("is_destroyed"):
                    continue
                u_lat = u.get("lat")
                u_lon = u.get("lon")
                if not u_lat or not u_lon:
                    continue

                dlat = math.radians(u_lat - unit_lat)
                dlon = math.radians(u_lon - unit_lon)
                a = (math.sin(dlat / 2) ** 2 +
                     math.cos(math.radians(unit_lat)) *
                     math.cos(math.radians(u_lat)) *
                     math.sin(dlon / 2) ** 2)
                dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                if dist_m <= 2000:
                    f_info = {
                        "name": u["name"],
                        "type": u.get("unit_type", "?"),
                        "distance_m": round(dist_m),
                        "strength": round(u.get("strength", 1.0), 2),
                        "coordinates": {
                            "lat": round(u_lat, 6),
                            "lon": round(u_lon, 6),
                        },
                    }
                    # Grid ref for friendly
                    if grid_service:
                        try:
                            f_info["grid_ref"] = grid_service.point_to_snail(u_lat, u_lon, depth=2)
                        except Exception:
                            pass
                    nearby_friendlies.append(f_info)

            nearby_friendlies.sort(key=lambda x: x["distance_m"])
            situation["nearby_friendlies"] = nearby_friendlies[:8]

        # ── Parent unit & subordinate units ────────────────
        parent_id = unit_dict.get("parent_unit_id")
        if parent_id:
            for u in all_units:
                if u.get("id") == parent_id:
                    situation["parent_unit"] = {
                        "name": u["name"],
                        "type": u.get("unit_type", "?"),
                    }
                    break

        # Subordinate units
        subordinates = []
        for u in all_units:
            if u.get("parent_unit_id") == unit_id and not u.get("is_destroyed"):
                sub_info = {
                    "name": u["name"],
                    "type": u.get("unit_type", "?"),
                    "strength": round(u.get("strength", 1.0), 2),
                }
                subordinates.append(sub_info)
        if subordinates:
            situation["subordinate_units"] = subordinates

        # ── Combat status derived from state ───────────────
        suppression = unit_dict.get("suppression", 0.0)
        strength = unit_dict.get("strength", 1.0)
        morale = unit_dict.get("morale", 1.0)
        combat_status = "nominal"
        if suppression > 0.7:
            combat_status = "heavily_suppressed"
        elif suppression > 0.3:
            combat_status = "under_fire"
        elif suppression > 0.1:
            combat_status = "light_fire"

        if strength < 0.25:
            combat_status = "combat_ineffective"
        elif strength < 0.5:
            combat_status = "heavy_casualties" if combat_status == "nominal" else combat_status

        if morale < 0.15:
            combat_status = "broken"
        elif morale < 0.3:
            combat_status = "shaken"

        situation["combat_status"] = combat_status

        # ── Ammo projection ───────────────────────────────
        ammo = unit_dict.get("ammo", 1.0)
        if ammo < 1.0:
            # Estimate rounds of fire remaining at ~0.01 per tick consumption
            ammo_ticks = round(ammo / 0.01) if ammo > 0 else 0
            ammo_level = "critical" if ammo < 0.2 else ("low" if ammo < 0.5 else "adequate")
            situation["ammo_status"] = {
                "level": ammo_level,
                "percentage": round(ammo * 100),
                "est_fire_ticks": ammo_ticks,
            }

        # ── Supply chain proximity ─────────────────────────
        if unit_lat and unit_lon:
            supply_units = {}
            supply_types = {
                "logistics_unit": "supply",
                "field_hospital": "medical",
                "command_post": "command",
                "headquarters": "command",
            }
            for u in all_units:
                if u.get("side") != issuer_side or u.get("is_destroyed"):
                    continue
                ut = u.get("unit_type", "")
                label = supply_types.get(ut)
                if not label:
                    continue
                u_lat, u_lon = u.get("lat"), u.get("lon")
                if not u_lat or not u_lon:
                    continue
                dlat = math.radians(u_lat - unit_lat)
                dlon = math.radians(u_lon - unit_lon)
                a = (math.sin(dlat / 2) ** 2 +
                     math.cos(math.radians(unit_lat)) *
                     math.cos(math.radians(u_lat)) *
                     math.sin(dlon / 2) ** 2)
                dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
                if label not in supply_units or dist_m < supply_units[label]["distance_m"]:
                    supply_units[label] = {
                        "name": u["name"],
                        "distance_m": round(dist_m),
                    }
            if supply_units:
                situation["nearest_support"] = supply_units

        # ── Nearby map objects (discovered by this side) ───
        if unit_lat and unit_lon:
            try:
                from geoalchemy2.shape import to_shape as to_shape_obj

                map_objs = preloaded.get("map_objects") if preloaded else None
                if map_objs is None:
                    from backend.models.map_object import MapObject

                    if issuer_side == "blue":
                        discovery_filter = MapObject.discovered_by_blue == True
                    else:
                        discovery_filter = MapObject.discovered_by_red == True

                    map_objs_result = await db.execute(
                        select(MapObject).where(
                            MapObject.session_id == session_id,
                            MapObject.is_active == True,
                            discovery_filter,
                        )
                    )
                    map_objs = map_objs_result.scalars().all()

                nearby_objects = []
                for obj in map_objs:
                    obj_lat, obj_lon = None, None
                    if obj.geometry:
                        try:
                            shape = to_shape_obj(obj.geometry)
                            centroid = shape.centroid
                            obj_lat, obj_lon = centroid.y, centroid.x
                        except Exception:
                            continue

                    if obj_lat and obj_lon:
                        dlat = math.radians(obj_lat - unit_lat)
                        dlon = math.radians(obj_lon - unit_lon)
                        a = (math.sin(dlat / 2) ** 2 +
                             math.cos(math.radians(unit_lat)) *
                             math.cos(math.radians(obj_lat)) *
                             math.sin(dlon / 2) ** 2)
                        dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                        if dist_m <= 3000:  # within 3km
                            obj_info = {
                                "type": obj.object_type,
                                "category": obj.object_category.value if hasattr(obj.object_category, 'value') else obj.object_category,
                                "distance_m": round(dist_m),
                                "side": obj.side.value if hasattr(obj.side, 'value') else obj.side,
                                "coordinates": {
                                    "lat": round(obj_lat, 6),
                                    "lon": round(obj_lon, 6),
                                },
                            }
                            if obj.label:
                                obj_info["label"] = obj.label
                            # Grid ref for object
                            if grid_service:
                                try:
                                    obj_info["grid_ref"] = grid_service.point_to_snail(obj_lat, obj_lon, depth=2)
                                except Exception:
                                    pass
                            nearby_objects.append(obj_info)

                nearby_objects.sort(key=lambda x: x["distance_m"])
                if nearby_objects:
                    situation["nearby_objects"] = nearby_objects[:10]
            except Exception:
                pass

        # ── Recent events for this unit (last 5 ticks) ────
        if unit_id:
            try:
                from backend.models.event import Event

                current_tick = preloaded.get("current_tick", 0) if preloaded else 0
                if not current_tick:
                    from backend.models.session import Session as Sess2
                    tick_result = await db.execute(
                        select(Sess2.tick).where(Sess2.id == session_id)
                    )
                    tick_row = tick_result.first()
                    current_tick = tick_row[0] if tick_row else 0

                events_result = await db.execute(
                    select(Event).where(
                        Event.session_id == session_id,
                        Event.tick >= max(0, current_tick - 5),
                        or_(
                            Event.actor_unit_id == uuid.UUID(unit_id),
                            Event.target_unit_id == uuid.UUID(unit_id),
                        ),
                        Event.visibility.in_(["all", issuer_side]),
                    ).order_by(desc(Event.tick)).limit(10)
                )
                events = events_result.scalars().all()
                situation["recent_events"] = [
                    {
                        "tick": e.tick,
                        "type": e.event_type,
                        "summary": e.text_summary or "",
                    }
                    for e in events
                ]
            except Exception:
                pass

        # ── Recent orders to this unit (last 5) ───────────
        if unit_id:
            try:
                uid = uuid.UUID(unit_id)
                orders_result = await db.execute(
                    select(Order).where(
                        Order.session_id == session_id,
                        Order.target_unit_ids.any(uid),
                    ).order_by(desc(Order.issued_at)).limit(5)
                )
                recent_orders = orders_result.scalars().all()
                situation["recent_orders"] = [
                    {
                        "text": (o.original_text or "")[:80],
                        "status": o.status.value,
                        "type": o.order_type,
                    }
                    for o in recent_orders
                ]
            except Exception:
                pass

        # ── Nearby planning overlays (markers, arrows, labels) ───
        if unit_lat and unit_lon:
            try:
                from geoalchemy2.shape import to_shape as to_shape_ovl

                overlays = preloaded.get("overlays", []) if preloaded else None
                if overlays is None:
                    from backend.models.overlay import PlanningOverlay
                    ovl_result = await db.execute(
                        select(PlanningOverlay).where(
                            PlanningOverlay.session_id == session_id,
                            PlanningOverlay.side == issuer_side,
                        )
                    )
                    overlays = ovl_result.scalars().all()
                nearby_overlays = []
                for ovl in overlays:
                    if not ovl.geometry:
                        continue
                    try:
                        shape = to_shape_ovl(ovl.geometry)
                        centroid = shape.centroid
                        ovl_lat, ovl_lon = centroid.y, centroid.x
                    except Exception:
                        continue

                    dlat = math.radians(ovl_lat - unit_lat)
                    dlon = math.radians(ovl_lon - unit_lon)
                    a = (math.sin(dlat / 2) ** 2 +
                         math.cos(math.radians(unit_lat)) *
                         math.cos(math.radians(ovl_lat)) *
                         math.sin(dlon / 2) ** 2)
                    dist_m = 6371000 * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

                    if dist_m <= 5000:  # within 5km
                        ovl_info = {
                            "type": ovl.overlay_type.value if hasattr(ovl.overlay_type, 'value') else str(ovl.overlay_type),
                            "label": ovl.label or "",
                            "distance_m": round(dist_m),
                        }
                        if grid_service:
                            try:
                                ovl_info["grid_ref"] = grid_service.point_to_snail(ovl_lat, ovl_lon, depth=2)
                            except Exception:
                                pass
                        nearby_overlays.append(ovl_info)

                nearby_overlays.sort(key=lambda x: x["distance_m"])
                if nearby_overlays:
                    situation["nearby_overlays"] = nearby_overlays[:8]
            except Exception:
                pass

        return situation

    # ── Geometry helpers ──────────────────────────────────

    @staticmethod
    def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Haversine distance in meters between two lat/lon points."""
        import math
        R = 6_371_000
        rlat1, rlon1 = math.radians(lat1), math.radians(lon1)
        rlat2, rlon2 = math.radians(lat2), math.radians(lon2)
        dlat = rlat2 - rlat1
        dlon = rlon2 - rlon1
        a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
        return 2 * R * math.asin(math.sqrt(a))

    @staticmethod
    def _bearing_deg(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Initial bearing in degrees from (lat1,lon1) to (lat2,lon2)."""
        import math
        rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
        dlon = math.radians(lon2 - lon1)
        x = math.sin(dlon) * math.cos(rlat2)
        y = math.cos(rlat1) * math.sin(rlat2) - math.sin(rlat1) * math.cos(rlat2) * math.cos(dlon)
        return math.degrees(math.atan2(x, y)) % 360

    @staticmethod
    def _destination_point(lat: float, lon: float, bearing_deg: float, distance_m: float) -> tuple[float, float]:
        """Compute destination lat/lon given start point, bearing (degrees), and distance (meters)."""
        import math
        R = 6_371_000
        d = distance_m / R
        br = math.radians(bearing_deg)
        lat1 = math.radians(lat)
        lon1 = math.radians(lon)
        lat2 = math.asin(
            math.sin(lat1) * math.cos(d) + math.cos(lat1) * math.sin(d) * math.cos(br)
        )
        lon2 = lon1 + math.atan2(
            math.sin(br) * math.sin(d) * math.cos(lat1),
            math.cos(d) - math.sin(lat1) * math.sin(lat2),
        )
        return math.degrees(lat2), math.degrees(lon2)

    @staticmethod
    def _bearing_to_compass(bearing: float) -> str:
        """Convert bearing in degrees to 16-point compass direction."""
        directions = [
            "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
            "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
        ]
        idx = round(bearing / 22.5) % 16
        return directions[idx]

    # ── Enriched Context Builders ─────────────────────────────────

    async def _build_terrain_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        units_context: list[dict],
        issuer_side: str,
    ) -> str:
        """Build terrain description around friendly unit positions."""
        try:
            from backend.models.terrain_cell import TerrainCell

            result = await db.execute(
                select(TerrainCell.snail_path, TerrainCell.terrain_type,
                       TerrainCell.elevation_m, TerrainCell.slope_deg,
                       TerrainCell.centroid_lat, TerrainCell.centroid_lon)
                .where(TerrainCell.session_id == session_id)
                .limit(500)
            )
            cells = result.all()
            if not cells:
                return "No terrain data available."

            # Summarize terrain types
            type_counts: dict[str, int] = {}
            for _, ttype, *_ in cells:
                type_counts[ttype] = type_counts.get(ttype, 0) + 1

            lines = ["Terrain in operations area:"]
            for ttype, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  - {ttype}: {count} cells")

            elevations = [c[2] for c in cells if c[2] is not None]
            slopes = [c[3] for c in cells if c[3] is not None]
            if elevations:
                lines.append(
                    "Elevation profile: min {:.0f}m, avg {:.0f}m, max {:.0f}m".format(
                        min(elevations), sum(elevations) / len(elevations), max(elevations)
                    )
                )
            if slopes:
                steep = sum(1 for s in slopes if s >= 20)
                lines.append(
                    f"Slope profile: avg {sum(slopes) / len(slopes):.1f}°, steep-cells(>=20°)={steep}"
                )

            # Find terrain near friendly units (within ~500m)
            friendly_units = [u for u in units_context
                              if u.get("side") == issuer_side
                              and not u.get("is_destroyed")
                              and u.get("lat") is not None
                              and u.get("lon") is not None]
            if friendly_units and cells:
                lines.append("Terrain near friendly units:")
                for u in friendly_units[:6]:
                    u_lat, u_lon = u["lat"], u["lon"]
                    nearest = None
                    nearest_dist = float('inf')
                    for sp, tt, elev, slope_deg, clat, clon in cells:
                        if clat is not None and clon is not None:
                            d = ((clat - u_lat) * 111320) ** 2 + ((clon - u_lon) * 74000) ** 2
                            if d < nearest_dist:
                                nearest_dist = d
                                nearest = (sp, tt, elev, slope_deg)
                    if nearest:
                        elev_str = f", elev {nearest[2]:.0f}m" if nearest[2] is not None else ""
                        slope_str = f", slope {nearest[3]:.1f}°" if nearest[3] is not None else ""
                        lines.append(
                            f"  - {u['name']}: {nearest[1]} ({nearest[0]}{elev_str}{slope_str})"
                        )

            return "\n".join(lines)
        except Exception:
            return "No terrain data available."

    async def _build_contacts_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        grid_service: Any = None,
    ) -> str:
        """Build known enemy contacts description for LLM context."""
        try:
            from backend.models.contact import Contact

            result = await db.execute(
                select(Contact).where(
                    Contact.session_id == session_id,
                    Contact.observing_side == issuer_side,
                    Contact.is_stale == False,
                ).limit(20)
            )
            contacts = result.scalars().all()
            if not contacts:
                return "No known enemy contacts."

            lines = [f"Known enemy contacts ({len(contacts)} active):"]
            for c in contacts:
                c_lat, c_lon = None, None
                if c.location_estimate:
                    try:
                        pt = to_shape(c.location_estimate)
                        c_lat, c_lon = pt.y, pt.x
                    except Exception:
                        pass
                pos_str = (
                    f" at ({c_lat:.4f}, {c_lon:.4f})"
                    if c_lat is not None and c_lon is not None
                    else ""
                )
                grid_str = ""
                if (
                    grid_service is not None
                    and c_lat is not None
                    and c_lon is not None
                ):
                    try:
                        grid_str = f", grid {grid_service.point_to_snail(c_lat, c_lon, depth=2)}"
                    except Exception:
                        pass

                conf_str = (
                    f"conf={c.confidence:.0%}"
                    if c.confidence is not None
                    else "conf=?"
                )
                acc_str = (
                    f", ±{c.location_accuracy_m:.0f}m"
                    if c.location_accuracy_m is not None
                    else ""
                )
                seen_tick = f", tick={c.last_seen_tick}" if c.last_seen_tick is not None else ""
                etype = c.estimated_type or "unknown"
                esize = c.estimated_size or ""
                source = c.source or "unknown"
                lines.append(
                    f"  - {etype} {esize}{pos_str}{grid_str} "
                    f"[{conf_str}{acc_str}{seen_tick}, src={source}]"
                )

            return "\n".join(lines)
        except Exception:
            return "No known enemy contacts."

    async def _load_session_and_scenario(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> tuple:
        """Load session + scenario in one pass. Returns (session_obj, scenario_obj)."""
        try:
            from backend.models.session import Session as Sess
            from backend.models.scenario import Scenario

            sess_result = await db.execute(
                select(Sess).where(Sess.id == session_id)
            )
            session_obj = sess_result.scalar_one_or_none()
            scenario_obj = None
            if session_obj:
                scen_result = await db.execute(
                    select(Scenario).where(Scenario.id == session_obj.scenario_id)
                )
                scenario_obj = scen_result.scalar_one_or_none()
            return (session_obj, scenario_obj)
        except Exception:
            return (None, None)

    async def _build_objectives_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        _cached: tuple | None = None,
    ) -> str:
        """Build mission objectives description for LLM context."""
        try:
            if _cached:
                session_obj, scenario = _cached
            else:
                from backend.models.session import Session as Sess
                from backend.models.scenario import Scenario

                sess_result = await db.execute(
                    select(Sess).where(Sess.id == session_id)
                )
                session_obj = sess_result.scalar_one_or_none()
                if not session_obj:
                    return "No objectives defined."
                scen_result = await db.execute(
                    select(Scenario).where(Scenario.id == session_obj.scenario_id)
                )
                scenario = scen_result.scalar_one_or_none()

            if not scenario:
                return "No objectives defined."

            lines = []
            if scenario.description:
                lines.append(f"Mission: {scenario.description[:300]}")

            if scenario.objectives:
                obj = scenario.objectives
                if isinstance(obj, dict):
                    if obj.get("victory_blue"):
                        lines.append(f"Blue victory: {obj['victory_blue'][:200]}")
                    if obj.get("victory_red"):
                        lines.append(f"Red victory: {obj['victory_red'][:200]}")
                    if obj.get("mission"):
                        lines.append(f"Objective: {obj['mission'][:200]}")

            if scenario.environment:
                env = scenario.environment
                parts = []
                if env.get("weather"):
                    parts.append(f"weather={env['weather']}")
                if env.get("time_of_day"):
                    parts.append(f"time={env['time_of_day']}")
                if parts:
                    lines.append(f"Conditions: {', '.join(parts)}")

            return "\n".join(lines) if lines else "No specific objectives defined."
        except Exception:
            return "No objectives defined."

    async def _build_environment_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        _cached: tuple | None = None,
    ) -> str:
        """Build weather/conditions/time context for the parser prompt."""
        try:
            if _cached:
                session_obj, scenario = _cached
            else:
                from backend.models.session import Session as Sess
                from backend.models.scenario import Scenario

                sess_result = await db.execute(
                    select(Sess).where(Sess.id == session_id)
                )
                session_obj = sess_result.scalar_one_or_none()
                scenario = None
                if session_obj:
                    scen_result = await db.execute(
                        select(Scenario).where(Scenario.id == session_obj.scenario_id)
                    )
                    scenario = scen_result.scalar_one_or_none()

            if not session_obj:
                return "No environment data available."

            lines = []
            if session_obj.current_time:
                lines.append(
                    "Current simulation time: {} (tick={}, tick_interval={}s)".format(
                        session_obj.current_time.isoformat(),
                        session_obj.tick,
                        session_obj.tick_interval,
                    )
                )
            else:
                lines.append(
                    f"Current simulation tick: {session_obj.tick} (tick_interval={session_obj.tick_interval}s)"
                )

            if session_obj.settings:
                speed = session_obj.settings.get("speed")
                if speed is not None:
                    lines.append(f"Session speed setting: {speed}")

            if scenario and scenario.environment:
                env = scenario.environment
                weather_parts = []
                for key in (
                    "weather",
                    "time_of_day",
                    "visibility",
                    "light_level",
                    "wind",
                    "temperature",
                    "precipitation",
                    "clouds",
                ):
                    val = env.get(key)
                    if val is not None and val != "":
                        weather_parts.append(f"{key}={val}")
                if weather_parts:
                    lines.append("Environment: " + ", ".join(weather_parts))
                elif env:
                    lines.append(f"Environment raw: {str(env)[:400]}")

            return "\n".join(lines)
        except Exception:
            return "No environment data available."

    async def _build_orders_history_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        units_context: list[dict],
    ) -> str:
        """Build side-visible order history so parser can preserve continuity."""
        try:
            unit_name_by_id = {
                u["id"]: u.get("name", u["id"])
                for u in units_context
                if u.get("id")
            }
            friendly_ids = {
                u["id"]
                for u in units_context
                if u.get("side") == issuer_side and not u.get("is_destroyed")
            }

            result = await db.execute(
                select(Order).where(
                    Order.session_id == session_id,
                    Order.issued_by_side == issuer_side,
                ).order_by(desc(Order.issued_at)).limit(40)
            )
            orders = result.scalars().all()
            if not orders:
                return "No prior own-side orders."

            lines = [f"Recent own-side orders ({len(orders)}):"]
            latest_by_unit: dict[str, Order] = {}

            for o in orders:
                target_ids = [str(uid) for uid in (o.target_unit_ids or [])]
                target_names = [unit_name_by_id.get(uid, uid[:8]) for uid in target_ids]
                for uid in target_ids:
                    if uid in friendly_ids and uid not in latest_by_unit:
                        latest_by_unit[uid] = o

                order_type = (
                    o.order_type
                    or (o.parsed_order or {}).get("type")
                    or "unknown"
                )
                target_str = ", ".join(target_names[:4]) if target_names else "unspecified units"
                if len(target_names) > 4:
                    target_str += f" +{len(target_names) - 4}"

                issued = o.issued_at.isoformat() if o.issued_at else "unknown_time"
                text = (o.original_text or "").replace("\n", " ").strip()
                if len(text) > 130:
                    text = text[:127] + "..."
                intent_action = (o.parsed_intent or {}).get("action")
                intent_str = f", intent={intent_action}" if intent_action else ""
                lines.append(
                    f"  - {issued}: {order_type}{intent_str} -> {target_str} [{o.status.value}] | \"{text}\""
                )

            if latest_by_unit:
                lines.append("Latest order per friendly unit:")
                ordered_ids = sorted(
                    friendly_ids,
                    key=lambda uid: unit_name_by_id.get(uid, uid),
                )
                for uid in ordered_ids[:20]:
                    latest = latest_by_unit.get(uid)
                    if latest is None:
                        continue
                    order_type = (
                        latest.order_type
                        or (latest.parsed_order or {}).get("type")
                        or "unknown"
                    )
                    issued = latest.issued_at.isoformat() if latest.issued_at else "unknown_time"
                    lines.append(
                        f"  - {unit_name_by_id.get(uid, uid[:8])}: {order_type} [{latest.status.value}] at {issued}"
                    )

            return "\n".join(lines)
        except Exception:
            return "No prior own-side orders."

    async def _build_radio_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
    ) -> str:
        """Build recent radio/chat traffic context for parser disambiguation."""
        try:
            from backend.models.chat_message import ChatMessage

            query = select(ChatMessage).where(ChatMessage.session_id == session_id)
            if issuer_side in ("blue", "red"):
                query = query.where(
                    or_(
                        ChatMessage.side == issuer_side,
                        ChatMessage.side == "all",
                    )
                )

            result = await db.execute(
                query.order_by(desc(ChatMessage.created_at)).limit(40)
            )
            messages = list(result.scalars().all())
            if not messages:
                return "No recent radio/chat traffic."

            # Chronological (oldest -> newest) improves continuity interpretation.
            messages.reverse()
            lines = [f"Recent radio/chat traffic ({len(messages)} msgs):"]
            for m in messages:
                text = (m.text or "").replace("\n", " ").strip()
                if len(text) > 170:
                    text = text[:167] + "..."
                ts = (
                    m.game_time.isoformat()
                    if m.game_time
                    else (m.created_at.isoformat() if m.created_at else "unknown_time")
                )
                if (m.sender_name or "").startswith("📋"):
                    msg_type = "ORDER_NET"
                elif (m.sender_name or "").startswith("📻"):
                    msg_type = "UNIT_RADIO"
                else:
                    msg_type = "CHAT"
                lines.append(
                    f"  - {ts} [{msg_type}] {m.sender_name} -> {m.recipient}: {text}"
                )
            return "\n".join(lines)
        except Exception:
            return "No recent radio/chat traffic."

    async def _build_reports_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
        units_context: list[dict],
    ) -> str:
        """Build recent SITREP/SPOTREP/etc. for better command interpretation."""
        try:
            from backend.models.report import Report, ReportSide

            unit_name_by_id = {
                u["id"]: u.get("name", u["id"])
                for u in units_context
                if u.get("id")
            }
            try:
                to_side = ReportSide(issuer_side)
            except Exception:
                to_side = ReportSide.blue

            result = await db.execute(
                select(Report).where(
                    Report.session_id == session_id,
                    Report.to_side == to_side,
                ).order_by(desc(Report.created_at)).limit(20)
            )
            reports = result.scalars().all()
            if not reports:
                return "No recent operational reports."

            lines = [f"Recent operational reports ({len(reports)}):"]
            for rep in reports:
                ts = (
                    rep.game_timestamp.isoformat()
                    if rep.game_timestamp
                    else (rep.created_at.isoformat() if rep.created_at else "unknown_time")
                )
                from_name = (
                    unit_name_by_id.get(str(rep.from_unit_id), str(rep.from_unit_id)[:8])
                    if rep.from_unit_id
                    else "HQ/unknown"
                )
                text = (rep.text or "").replace("\n", " ").strip()
                if len(text) > 180:
                    text = text[:177] + "..."
                lines.append(
                    f"  - {ts} [{rep.channel}] from {from_name}: {text}"
                )
            return "\n".join(lines)
        except Exception:
            return "No recent operational reports."

    @staticmethod
    def _build_map_objects_prompt_context(map_objects_context: list[dict]) -> str:
        """Format known map objects as compact prompt context."""
        if not map_objects_context:
            return "No known map objects."

        lines = [f"Known map objects / points of interest ({len(map_objects_context)}):"]
        for obj in map_objects_context[:25]:
            name = obj.get("name") or obj.get("label") or obj.get("object_type", "object")
            object_type = obj.get("object_type", "object")
            obj_side = obj.get("side", "neutral")
            obj_cat = obj.get("object_category", "unknown")
            lat = obj.get("lat")
            lon = obj.get("lon")
            coord = ""
            if lat is not None and lon is not None:
                coord = f" at ({lat:.4f}, {lon:.4f})"
            lines.append(
                f"  - {name} ({object_type}, category={obj_cat}, side={obj_side}){coord}"
            )
        return "\n".join(lines)

    @staticmethod
    def _build_friendly_status_context(
        units_context: list[dict],
        issuer_side: str,
    ) -> str:
        """Build friendly force summary for LLM context."""
        friendlies = [u for u in units_context
                      if u.get("side") == issuer_side and not u.get("is_destroyed")]
        if not friendlies:
            return "No friendly units."

        # Summary stats
        total = len(friendlies)
        avg_strength = sum(u.get("strength", 1.0) for u in friendlies) / total
        avg_ammo = sum(u.get("ammo", 1.0) for u in friendlies) / total
        avg_morale = sum(u.get("morale", 1.0) for u in friendlies) / total

        lines = [f"Friendly forces ({total} units): avg strength={avg_strength:.0%}, ammo={avg_ammo:.0%}, morale={avg_morale:.0%}"]

        # Per-unit status (brief)
        for u in friendlies[:15]:
            task = u.get("current_task", {})
            task_str = task.get("type", "idle") if task else "idle"
            strength = u.get("strength", 1.0)
            ammo = u.get("ammo", 1.0)
            morale = u.get("morale", 1.0)
            comms = u.get("comms_status", "operational")
            status_flags = []
            if strength < 0.5:
                status_flags.append("WEAK")
            if ammo < 0.3:
                status_flags.append("LOW AMMO")
            if morale < 0.4:
                status_flags.append("LOW MORALE")
            if u.get("suppression", 0) > 0.5:
                status_flags.append("SUPPRESSED")
            if comms != "operational":
                status_flags.append(f"COMMS={comms}")
            flag_str = f" [{', '.join(status_flags)}]" if status_flags else ""

            pos_str = ""
            if u.get("lat") is not None and u.get("lon") is not None:
                pos_str = f", pos={u['lat']:.4f},{u['lon']:.4f}"
            lines.append(
                f"  - {u['name']} ({u.get('unit_type', '?')}): {task_str}, "
                f"str={strength:.0%}, ammo={ammo:.0%}, morale={morale:.0%}{pos_str}{flag_str}"
            )

        return "\n".join(lines)


# Singleton
order_service = OrderService()


