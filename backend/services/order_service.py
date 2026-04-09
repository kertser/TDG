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

        # ── 2. Parse via LLM ────────────────────────────────────
        parsed = await order_parser.parse(
            original_text=original_text,
            units=units_context,
            grid_info=grid_info,
            game_time=game_time,
            issuer_side=issuer_side,
        )

        # Save parsed order immediately
        order.parsed_order = parsed.model_dump(mode="json", exclude_none=True)

        # ── 3. Route by classification ──────────────────────────
        result = OrderParseResult(parsed=parsed)

        if parsed.classification == MessageClassification.command:
            await self._process_command(order, parsed, result, units_context,
                                        grid_service, session_id, db, issuer_side)

        elif parsed.classification == MessageClassification.status_request:
            await self._process_status_request(order, parsed, result, units_context,
                                                session_id, db, issuer_side, grid_service)

        elif parsed.classification == MessageClassification.acknowledgment:
            order.status = OrderStatus.completed
            order.completed_at = datetime.now(timezone.utc)

        elif parsed.classification == MessageClassification.status_report:
            order.status = OrderStatus.completed
            order.completed_at = datetime.now(timezone.utc)

        elif parsed.classification == MessageClassification.unclear:
            order.status = OrderStatus.failed
            # Generate clarification request from target units
            matched = self._match_units(parsed.target_unit_refs, units_context, issuer_side)
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

        # If still no units, mark failed
        if not matched_units:
            order.status = OrderStatus.failed
            order.parsed_intent = {"error": "no_target_units_matched"}
            return

        # Update order.target_unit_ids from matched units (override if LLM found them)
        if not order.target_unit_ids and matched_units:
            order.target_unit_ids = [uuid.UUID(u["id"]) for u in matched_units]

        # ── Resolve locations ─────────────────────────────
        resolver = LocationResolver(grid_service=grid_service)
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

        # ── Check unit states & generate responses ────────
        all_ok = True
        for unit_dict in matched_units:
            resp_type, reason = response_generator.determine_response_type(parsed, unit_dict)

            if resp_type == ResponseType.no_response:
                all_ok = False
                continue
            elif resp_type == ResponseType.unable:
                all_ok = False

            # Build situational awareness for status and command acknowledgments
            status_text = ""
            if resp_type in (ResponseType.status, ResponseType.wilco, ResponseType.ack):
                situation = await self._build_unit_situation(
                    unit_dict, session_id, issuer_side, units_context,
                    db, grid_service,
                )
                if resp_type == ResponseType.status:
                    # Full status report
                    status_text = response_generator.generate_status_report(
                        unit_dict, parsed.language.value, situation=situation,
                    )
                else:
                    # Brief situation for acknowledgments (position + key info)
                    status_text = response_generator.generate_brief_sitrep(
                        unit_dict, parsed.language.value, situation=situation,
                    )

            resp = response_generator.generate_response(
                parsed=parsed,
                unit=unit_dict,
                response_type=resp_type,
                reason_key=reason,
                status_text=status_text,
            )
            if resp:
                result.responses.append(resp)

        # ── Set order status ──────────────────────────────
        if all_ok and task:
            order.status = OrderStatus.validated
            order.validated_at = datetime.now(timezone.utc)
        elif task:
            # Some units can't comply but order is still valid
            order.status = OrderStatus.validated
            order.validated_at = datetime.now(timezone.utc)
        else:
            order.status = OrderStatus.failed

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

        # If no specific units, report from all own-side units
        if not matched:
            matched = [u for u in units_context
                       if u.get("side") == issuer_side and not u.get("is_destroyed")]

        for unit_dict in matched:
            # Build situational awareness for rich status reports
            situation = await self._build_unit_situation(
                unit_dict, session_id, issuer_side, units_context,
                db, grid_service,
            )
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

        # For commands that don't need a location (halt, regroup, report_status, disengage)
        if parsed.order_type.value in ("halt", "regroup", "report_status", "disengage"):
            return task

        # Commands that need a location but don't have one — still valid for
        # defend (defend current position) and observe (observe from current)
        if "target_location" not in task:
            if parsed.order_type.value in ("defend", "observe"):
                return task
            # For move/attack without location, intent might provide it
            if intent and hasattr(intent, "action"):
                task["intent_action"] = intent.action
            return task  # Return task anyway — engine will handle as best it can

        return task

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


# Singleton
order_service = OrderService()


