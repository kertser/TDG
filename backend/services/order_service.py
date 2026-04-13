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
import uuid
from datetime import datetime, timezone
from typing import Any

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point
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
    ResponseType,
)
from backend.services.order_parser import order_parser
from backend.services.location_resolver import LocationResolver
from backend.services.intent_interpreter import intent_interpreter
from backend.services.response_generator import response_generator

logger = logging.getLogger(__name__)

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

        # ── 1. Gather context ────────────────────────────────────
        units_context = await self._load_units_context(session_id, db)
        grid_info, grid_service = await self._load_grid(session_id, db)
        game_time = await self._get_game_time(session_id, db)
        map_objects_context = await self._load_map_objects_context(session_id, db)

        # Load elevation peaks for height references in orders
        elevation_peaks = await self._load_elevation_peaks(session_id, db)
        if elevation_peaks and grid_info:
            grid_info["height_tops"] = [
                {"label": p["label"], "label_ru": p["label_ru"],
                 "elevation_m": p["elevation_m"], "snail_path": p.get("snail_path", "")}
                for p in elevation_peaks[:30]  # Limit to top 30 peaks
            ]

        # ── 1b. Build enriched context for LLM ───────────────────
        terrain_ctx = await self._build_terrain_context(session_id, db, units_context, issuer_side)
        contacts_ctx = await self._build_contacts_context(session_id, db, issuer_side)
        objectives_ctx = await self._build_objectives_context(session_id, db)
        friendly_ctx = self._build_friendly_status_context(units_context, issuer_side)

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
        matched_units = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        # If no units matched but we have target_unit_ids on the order, use those
        if not matched_units and order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched_units.append(u)

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
        if has_contact_target and unit_pos:
            try:
                from backend.models.contact import Contact
                from geoalchemy2.shape import to_shape as _to_shape
                from sqlalchemy import select as _select

                contacts_result = await db.execute(
                    _select(Contact).where(
                        Contact.session_id == session_id,
                        Contact.is_stale == False,
                        Contact.observing_side == order.issued_by_side,
                    )
                )
                nearby_contacts = list(contacts_result.scalars().all())
                best_dist = float('inf')
                best_contact_lat = None
                best_contact_lon = None
                best_contact_uid = None

                for c in nearby_contacts:
                    if c.location_estimate is None:
                        continue
                    try:
                        cpt = _to_shape(c.location_estimate)
                        c_lat, c_lon = cpt.y, cpt.x
                    except Exception:
                        continue
                    dlat = (c_lat - unit_pos[0]) * 111320.0
                    dlon = (c_lon - unit_pos[1]) * 74000.0
                    dist = (dlat**2 + dlon**2) ** 0.5
                    if dist < best_dist:
                        best_dist = dist
                        best_contact_lat = c_lat
                        best_contact_lon = c_lon
                        best_contact_uid = str(c.target_unit_id) if c.target_unit_id else None

                if best_contact_lat is not None:
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
                    logger.info(
                        "Resolved contact_target to (%f, %f) dist=%.0fm",
                        best_contact_lat, best_contact_lon, best_dist,
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
        task = self._build_engine_task(order, parsed, resolved, intent, grid_service=grid_service)
        result.engine_task = task

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
        MOVEABLE_TASK_TYPES = {"move", "attack", "advance", "disengage", "resupply"}
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
                               ResponseType.wilco_disengage, ResponseType.wilco_resupply,
                               ResponseType.wilco_observe, ResponseType.wilco_standby,
                               ResponseType.ack):
                situation = await self._build_unit_situation(
                    unit_dict, session_id, issuer_side, units_context,
                    db, grid_service,
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

                    if order_type_val in _movement_types and task_target_snail and own_grid:
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
        matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        # If no specific units referenced, use order's target_unit_ids
        if not matched and order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched.append(u)

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
        matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)

        # If no units matched from text, try order.target_unit_ids (from selected units)
        if not matched and order.target_unit_ids:
            for uid in order.target_unit_ids:
                for u in units_context:
                    if u.get("id") == str(uid):
                        matched.append(u)

        # Only fall back to all own-side units if BOTH text refs AND target_unit_ids are empty
        if not matched:
            matched = [u for u in units_context
                       if u.get("side") == issuer_side and not u.get("is_destroyed")]

        # Pre-build all situations in parallel for speed (each does ~10 DB queries)
        import asyncio as _asyncio_sr
        from backend.database import async_session_factory as _asf_sr

        async def _par_build_sit(ud):
            async with _asf_sr() as s:
                return ud.get("id", ""), await self._build_unit_situation(
                    ud, session_id, issuer_side, units_context, s, grid_service,
                )

        if matched:
            _sit_results = await _asyncio_sr.gather(*[_par_build_sit(ud) for ud in matched])
            _sit_map = {uid: sit for uid, sit in _sit_results}
        else:
            _sit_map = {}

        for unit_dict in matched:
            situation = _sit_map.get(unit_dict.get("id", ""), {})
            status_text = response_generator.generate_status_report(
                unit_dict, parsed.language.value, situation=situation,
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

    def _build_engine_task(
        self,
        order: Order,
        parsed: ParsedOrderData,
        resolved_locations: list[ResolvedLocation],
        intent: Any,
        grid_service: Any = None,
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

        # For commands that don't need a location (halt, regroup, report_status, disengage, withdraw, resupply)
        # Resupply target is auto-resolved by the resupply engine to the nearest supply source
        # request_fire target is resolved from nearest enemy contact at tick time
        if parsed.order_type.value in ("halt", "regroup", "report_status", "disengage", "withdraw", "resupply", "request_fire"):
            return task

        # Commands that need a location but don't have one — still valid for
        # defend (defend current position) and observe (observe from current)
        if "target_location" not in task:
            if parsed.order_type.value in ("defend", "observe"):
                return task
            # Attack with fire_at_will (engage targets of opportunity) — no location needed
            if parsed.order_type.value in ("attack", "engage") and task.get("engagement_rules") == "fire_at_will":
                return task
            # For move/attack without location, intent might provide it
            if intent and hasattr(intent, "action"):
                task["intent_action"] = intent.action
            return task  # Return task anyway — engine will handle as best it can

        return task

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
                load_or_build_static_graph,
                get_cached_graph,
                PathfindingService,
            )

            # Fast path: check in-memory cache first (avoids DB query for GridDefinition)
            cached_graph = get_cached_graph(sid_str)
            if cached_graph and cached_graph.get("centroids"):
                static_graph = cached_graph
                cell_centroids = cached_graph["centroids"]
            else:
                # Slow path: load GridDefinition from DB for persisted graph
                from backend.models.grid import GridDefinition
                gd_result = await db.execute(
                    select(GridDefinition).where(GridDefinition.session_id == session_id)
                )
                gd = gd_result.scalar_one_or_none()
                gd_settings = dict(gd.settings_json) if gd and gd.settings_json else None

                static_graph, cell_centroids = load_or_build_static_graph(
                    sid_str,
                    terrain_cells,
                    elevation_cells,
                    None,  # let it use cached or build
                    grid_service,
                    grid_def_settings_json=gd_settings,
                )

            _t_graph = _pf_time.monotonic()

            if not static_graph or not static_graph.get("centroids"):
                return None, True  # No graph → can't compute, not an error

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
                        score += 15

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
    ) -> list[dict]:
        """Load map objects with geometry for named location resolution (e.g. 'Airfield')."""
        try:
            from backend.models.map_object import MapObject
            from geoalchemy2.shape import to_shape

            result = await db.execute(
                select(MapObject).where(
                    MapObject.session_id == session_id,
                    MapObject.is_active == True,
                )
            )
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
                    "object_type": obj.object_type,
                    "name": name,
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

    async def _build_unit_situation(
        self,
        unit_dict: dict,
        session_id: uuid.UUID,
        issuer_side: str,
        all_units: list[dict],
        db: AsyncSession,
        grid_service: Any = None,
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
            situation["current_task"] = task_info

        # ── Terrain at position ───────────────────────────
        if unit_lat and unit_lon:
            try:
                from backend.models.terrain_cell import TerrainCell
                from backend.models.elevation_cell import ElevationCell

                # Find terrain cell by snail path
                if situation.get("grid_ref"):
                    # Try exact match first, then parent paths
                    snail_path = situation["grid_ref"]
                    tc = None
                    while snail_path and not tc:
                        tc_result = await db.execute(
                            select(TerrainCell).where(
                                TerrainCell.session_id == session_id,
                                TerrainCell.snail_path == snail_path,
                            )
                        )
                        tc = tc_result.scalar_one_or_none()
                        if not tc and "-" in snail_path:
                            snail_path = snail_path.rsplit("-", 1)[0]
                        else:
                            break

                    if tc:
                        terrain_info = {
                            "type": tc.terrain_type,
                            "modifiers": tc.modifiers or {},
                        }
                        if tc.elevation_m is not None:
                            terrain_info["elevation_m"] = round(tc.elevation_m, 1)
                        if tc.slope_deg is not None:
                            terrain_info["slope_deg"] = round(tc.slope_deg, 1)
                        situation["terrain"] = terrain_info

                    # Elevation data
                    ec_result = await db.execute(
                        select(ElevationCell).where(
                            ElevationCell.session_id == session_id,
                            ElevationCell.snail_path == situation["grid_ref"],
                        )
                    )
                    ec = ec_result.scalar_one_or_none()
                    if ec:
                        elev_info = {"elevation_m": round(ec.elevation_m, 1)}
                        if ec.slope_deg is not None:
                            elev_info["slope_deg"] = round(ec.slope_deg, 1)
                        if ec.aspect_deg is not None:
                            elev_info["aspect_deg"] = round(ec.aspect_deg, 1)
                        situation["elevation"] = elev_info
                        # Also set in terrain if not already
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
                from backend.models.terrain_cell import TerrainCell as TC2
                from sqlalchemy import func

                # Get the parent square of the current position
                current_ref = situation["grid_ref"]
                # Query nearby cells at the same depth level
                depth = current_ref.count("-")
                if depth > 0:
                    parent_path = current_ref.rsplit("-", 1)[0]
                    # Get all sibling cells (same parent)
                    sibs_result = await db.execute(
                        select(TC2.terrain_type, func.count(TC2.id)).where(
                            TC2.session_id == session_id,
                            TC2.snail_path.like(f"{parent_path}-%"),
                        ).group_by(TC2.terrain_type)
                    )
                    surrounding = {}
                    for row in sibs_result.all():
                        surrounding[row[0]] = row[1]
                    if surrounding:
                        situation["surrounding_terrain"] = surrounding
            except Exception:
                pass

        # ── Nearby height tops (elevation peaks) ───────────
        if unit_lat and unit_lon:
            try:
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
            from backend.models.session import Session
            from backend.models.scenario import Scenario

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
                from backend.models.map_object import MapObject
                from geoalchemy2.shape import to_shape as to_shape_obj
                from sqlalchemy import and_

                # Filter by discovery status for this side
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
                from backend.models.session import Session as Sess2

                # Get current tick
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
                from backend.models.overlay import PlanningOverlay
                from geoalchemy2.shape import to_shape as to_shape_ovl

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
                       TerrainCell.elevation_m, TerrainCell.centroid_lat,
                       TerrainCell.centroid_lon)
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

            # Find terrain near friendly units (within ~500m)
            friendly_units = [u for u in units_context
                              if u.get("side") == issuer_side
                              and not u.get("is_destroyed")
                              and u.get("lat")]
            if friendly_units and cells:
                lines.append("Terrain near friendly units:")
                for u in friendly_units[:6]:
                    u_lat, u_lon = u["lat"], u["lon"]
                    nearest = None
                    nearest_dist = float('inf')
                    for sp, tt, elev, clat, clon in cells:
                        if clat and clon:
                            d = ((clat - u_lat) * 111320) ** 2 + ((clon - u_lon) * 74000) ** 2
                            if d < nearest_dist:
                                nearest_dist = d
                                nearest = (sp, tt, elev)
                    if nearest:
                        elev_str = f", elev {nearest[2]:.0f}m" if nearest[2] else ""
                        lines.append(f"  - {u['name']}: {nearest[1]} ({nearest[0]}{elev_str})")

            return "\n".join(lines)
        except Exception:
            return "No terrain data available."

    async def _build_contacts_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
        issuer_side: str,
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
                pos_str = f" at ({c_lat:.4f}, {c_lon:.4f})" if c_lat else ""
                conf_str = f"conf={c.confidence:.0%}" if c.confidence else ""
                etype = c.estimated_type or "unknown"
                esize = c.estimated_size or ""
                lines.append(f"  - {etype} {esize}{pos_str} [{conf_str}, {c.source or '?'}]")

            return "\n".join(lines)
        except Exception:
            return "No known enemy contacts."

    async def _build_objectives_context(
        self,
        session_id: uuid.UUID,
        db: AsyncSession,
    ) -> str:
        """Build mission objectives description for LLM context."""
        try:
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
        for u in friendlies[:10]:
            task = u.get("current_task", {})
            task_str = task.get("type", "idle") if task else "idle"
            strength = u.get("strength", 1.0)
            ammo = u.get("ammo", 1.0)
            status_flags = []
            if strength < 0.5:
                status_flags.append("WEAK")
            if ammo < 0.3:
                status_flags.append("LOW AMMO")
            if u.get("suppression", 0) > 0.5:
                status_flags.append("SUPPRESSED")
            flag_str = f" [{', '.join(status_flags)}]" if status_flags else ""
            lines.append(f"  - {u['name']} ({u.get('unit_type', '?')}): {task_str}, str={strength:.0%}{flag_str}")

        return "\n".join(lines)


# Singleton
order_service = OrderService()


