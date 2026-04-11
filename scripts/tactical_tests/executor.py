"""
ScenarioExecutor — creates DB objects and runs the tick engine for one scenario.

For each scenario:
  1. Creates Scenario + Session + GridDefinition + Units + MapObjects + TerrainCells
  2. Injects pre-parsed orders at the appropriate ticks
  3. Runs run_tick() for N ticks, collecting snapshots
  4. Cleans up DB after test (unless --keep-data)
"""
from __future__ import annotations

import os
os.environ.setdefault("DEBUG", "false")

import logging
import time
import uuid
from datetime import datetime, timedelta, timezone

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import Point, Polygon, LineString
from sqlalchemy import select, delete, text
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import async_session_factory
from backend.models.scenario import Scenario
from backend.models.session import Session, SessionStatus
from backend.models.grid import GridDefinition
from backend.models.unit import Unit
from backend.models.order import Order, OrderStatus
from backend.models.order import LocationReference
from backend.models.contact import Contact
from backend.models.event import Event
from backend.models.map_object import MapObject, ObjectCategory
from backend.models.overlay import PlanningOverlay
from backend.engine.map_objects import get_category
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell
from backend.models.red_agent import RedAgent
from backend.models.report import Report
from backend.models.chat_message import ChatMessage

from backend.engine.tick import run_tick
from backend.engine.terrain import clear_terrain_cache

from scripts.tactical_tests.base import BaseScenario
from scripts.tactical_tests.collector import (
    UnitSnapshot, TickSnapshot, ScenarioResult, OrderSnapshot,
)

logger = logging.getLogger(__name__)


async def _run_llm_pipeline(order: Order, session_id, db, side: str) -> OrderSnapshot | None:
    """Route an order through the real LLM parsing pipeline."""
    try:
        from backend.services.order_service import OrderService
        service = OrderService()
        result = await service.process(order, session_id, db, issuer_side=side)

        snap = OrderSnapshot(
            original_text=order.original_text or "",
            target_unit_names=[],
            side=side,
            inject_tick=0,
        )

        if result and result.parsed:
            cls = result.parsed.classification
            snap.classification = cls.value if cls else None
            ot = result.parsed.order_type
            snap.order_type = ot.value if ot else None
            lang = result.parsed.language
            snap.language = lang.value if lang else None
            snap.confidence = result.parsed.confidence or 0.0
            snap.target_unit_refs = result.parsed.target_unit_refs or []
            snap.locations_resolved = [
                {"source": getattr(loc, "source_text", ""),
                 "ref_type": getattr(loc, "ref_type", ""),
                 "normalized": getattr(loc, "normalized_ref", ""),
                 "lat": getattr(loc, "lat", None),
                 "lon": getattr(loc, "lon", None)}
                for loc in (result.resolved_locations or [])
            ]
            # Determine model tier from confidence thresholds
            conf = snap.confidence
            if conf >= 0.80:
                snap.model_tier = "keyword"
            elif conf >= 0.50:
                snap.model_tier = "nano"
            else:
                snap.model_tier = "full"

            # Store full pipeline result for debugging
            snap.pipeline_result = {
                "classification": snap.classification,
                "order_type": snap.order_type,
                "language": snap.language,
                "confidence": snap.confidence,
                "target_unit_refs": snap.target_unit_refs,
                "matched_unit_ids": result.matched_unit_ids or [],
            }

        return snap
    except Exception as e:
        logger.exception("LLM pipeline error: %s", e)
        return OrderSnapshot(
            original_text=order.original_text or "",
            target_unit_names=[],
            side=side,
            inject_tick=0,
            error=str(e),
        )


class ScenarioExecutor:
    """Executes one tactical test scenario against the database."""

    def __init__(self, scenario: BaseScenario, keep_data: bool = False):
        self.scenario = scenario
        self.keep_data = keep_data
        self._session_id: uuid.UUID | None = None
        self._scenario_id: uuid.UUID | None = None
        self._unit_ids: dict[str, str] = {}  # name → UUID string
        self._unit_parents: dict[str, str] = {}  # child name → parent name

    async def run(self) -> ScenarioResult:
        """Execute the full scenario and return results."""
        result = ScenarioResult(
            scenario_name=self.scenario.name,
            scenario_description=self.scenario.description,
            ticks_run=0,
            category=self.scenario.category,
        )

        start_time = time.monotonic()

        try:
            async with async_session_factory() as db:
                # 1. Setup
                await self._setup(db)
                await db.commit()

                # 2. Run ticks
                total_ticks = self.scenario.ticks
                orders = self.scenario.build_orders(self._unit_ids)

                for tick_num in range(total_ticks):
                    try:
                        # Inject orders scheduled for this tick
                        await self._inject_orders(db, orders, tick_num, result)
                        await db.commit()

                        # Run the tick
                        tick_result = await run_tick(self._session_id, db)
                        await db.commit()

                        # Collect snapshot
                        snapshot = await self._collect_snapshot(db, tick_num, tick_result)
                        result.snapshots.append(snapshot)
                        result.ticks_run = tick_num + 1

                    except Exception as e:
                        result.errors.append(f"Tick {tick_num} failed: {type(e).__name__}: {e}")
                        logger.exception("Tick %d failed", tick_num)
                        # Try to collect whatever state we can
                        try:
                            await db.rollback()
                            snapshot = await self._collect_snapshot(db, tick_num, {})
                            result.snapshots.append(snapshot)
                        except Exception:
                            pass
                        break

                # 3. Cleanup
                if not self.keep_data:
                    await self._cleanup(db)
                    await db.commit()

        except Exception as e:
            result.errors.append(f"Setup/teardown error: {type(e).__name__}: {e}")
            logger.exception("Scenario %s failed", self.scenario.name)

        result.duration_seconds = time.monotonic() - start_time
        return result

    async def _setup(self, db: AsyncSession):
        """Create all DB objects for the scenario."""
        s_data = self.scenario.build_scenario_data()

        # Clear any cached terrain data
        clear_terrain_cache()

        # Create Scenario
        scenario = Scenario(
            title=s_data["title"],
            description=s_data.get("description", ""),
            map_center=from_shape(
                Point(s_data["map_center"]["lon"], s_data["map_center"]["lat"]),
                srid=4326
            ),
            map_zoom=s_data.get("map_zoom", 13),
            terrain_meta=s_data.get("terrain_meta"),
            objectives=s_data.get("objectives"),
            environment=s_data.get("environment"),
            grid_settings=s_data.get("grid_settings"),
            initial_units={"blue": [], "red": []},
        )
        db.add(scenario)
        await db.flush()
        self._scenario_id = scenario.id

        # Determine start time
        env = s_data.get("environment", {})
        tod = env.get("time_of_day", "morning")
        start_time = env.get("start_time")
        if start_time:
            if isinstance(start_time, str):
                game_start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            else:
                game_start = start_time
        else:
            # Default times based on time_of_day
            _tod_hours = {"morning": 8, "afternoon": 14, "evening": 18, "night": 23, "dawn": 5, "dusk": 19}
            hour = _tod_hours.get(tod, 8)
            game_start = datetime(2024, 6, 15, hour, 0, 0, tzinfo=timezone.utc)

        # Create Session
        session = Session(
            scenario_id=scenario.id,
            status=SessionStatus.running,
            tick=0,
            tick_interval=60,
            current_time=game_start,
        )
        db.add(session)
        await db.flush()
        self._session_id = session.id

        # Create GridDefinition
        gs = s_data["grid_settings"]
        grid_def = GridDefinition(
            session_id=session.id,
            origin=from_shape(
                Point(gs["origin_lon"], gs["origin_lat"]),
                srid=4326
            ),
            orientation_deg=gs.get("orientation_deg", 0),
            base_square_size_m=gs.get("base_square_size_m", 1000),
            columns=gs.get("columns", 8),
            rows=gs.get("rows", 8),
            labeling_scheme=gs.get("labeling_scheme", "alphanumeric"),
        )
        db.add(grid_def)

        # Create Units
        units_data = self.scenario.build_units()
        for u_data in units_data:
            parent_name = u_data.pop("parent_name", None)
            unit = Unit(
                session_id=session.id,
                side=u_data["side"],
                name=u_data["name"],
                unit_type=u_data["unit_type"],
                sidc=u_data.get("sidc", ""),
                position=from_shape(Point(u_data["lon"], u_data["lat"]), srid=4326),
                strength=u_data.get("strength", 1.0),
                ammo=u_data.get("ammo", 1.0),
                morale=u_data.get("morale", 0.9),
                move_speed_mps=u_data.get("move_speed_mps", 3.0),
                detection_range_m=u_data.get("detection_range_m", 1500),
                capabilities=u_data.get("capabilities"),
                heading_deg=u_data.get("heading_deg", 0.0),
            )
            db.add(unit)
            await db.flush()
            self._unit_ids[u_data["name"]] = str(unit.id)
            if parent_name:
                self._unit_parents[u_data["name"]] = parent_name

        # Set parent relationships
        for child_name, parent_name in self._unit_parents.items():
            child_id = self._unit_ids.get(child_name)
            parent_id = self._unit_ids.get(parent_name)
            if child_id and parent_id:
                result = await db.execute(
                    select(Unit).where(Unit.id == uuid.UUID(child_id))
                )
                child_unit = result.scalar_one_or_none()
                if child_unit:
                    child_unit.parent_unit_id = uuid.UUID(parent_id)

        # Create MapObjects
        for obj_data in self.scenario.build_map_objects():
            geom = obj_data.get("geometry")
            if geom:
                if isinstance(geom, dict):
                    geom_type = geom.get("type", "Point")
                    coords = geom.get("coordinates", [])
                    if geom_type == "Point":
                        shape = Point(coords[0], coords[1])
                    elif geom_type == "LineString":
                        shape = LineString(coords)
                    elif geom_type == "Polygon":
                        shape = Polygon(coords[0])
                    else:
                        shape = Point(0, 0)
                    geom_wkb = from_shape(shape, srid=4326)
                else:
                    geom_wkb = from_shape(geom, srid=4326)
            else:
                continue

            map_obj = MapObject(
                session_id=session.id,
                object_type=obj_data["object_type"],
                object_category=ObjectCategory(get_category(obj_data["object_type"])),
                geometry=geom_wkb,
                label=obj_data.get("label"),
                properties=obj_data.get("properties"),
                is_active=obj_data.get("is_active", True),
                discovered_by_blue=obj_data.get("discovered_by_blue", True),
                discovered_by_red=obj_data.get("discovered_by_red", True),
            )
            db.add(map_obj)

        # Create TerrainCells
        for tc_data in self.scenario.build_terrain_cells():
            tc = TerrainCell(
                session_id=session.id,
                snail_path=tc_data["snail_path"],
                depth=tc_data.get("depth", 1),
                terrain_type=tc_data["terrain_type"],
                modifiers=tc_data.get("modifiers"),
                source=tc_data.get("source", "manual"),
                confidence=tc_data.get("confidence", 1.0),
                centroid_lat=tc_data.get("centroid_lat", 0),
                centroid_lon=tc_data.get("centroid_lon", 0),
            )
            db.add(tc)

        # Create ElevationCells
        for ec_data in self.scenario.build_elevation_cells():
            ec = ElevationCell(
                session_id=session.id,
                snail_path=ec_data["snail_path"],
                depth=ec_data.get("depth", 1),
                elevation_m=ec_data.get("elevation_m", 0),
                slope_deg=ec_data.get("slope_deg", 0),
                aspect_deg=ec_data.get("aspect_deg"),
                centroid_lat=ec_data.get("centroid_lat", 0),
                centroid_lon=ec_data.get("centroid_lon", 0),
            )
            db.add(ec)

        await db.flush()

    async def _inject_orders(self, db: AsyncSession, orders: list[dict], current_tick: int,
                             result: ScenarioResult | None = None):
        """Inject orders scheduled for the current tick."""
        for o_data in orders:
            if o_data.get("inject_at_tick", 0) != current_tick:
                continue

            # Resolve unit names to IDs
            target_ids = []
            for name in o_data.get("target_unit_names", []):
                uid = self._unit_ids.get(name)
                if uid:
                    target_ids.append(uuid.UUID(uid))

            if not target_ids:
                logger.warning("Order has no valid targets: %s", o_data.get("original_text", "")[:50])
                continue

            use_llm = o_data.get("use_llm_pipeline", False)

            if use_llm:
                # Route through real LLM pipeline
                order = Order(
                    session_id=self._session_id,
                    issued_by_side=o_data.get("issued_by_side", "blue"),
                    target_unit_ids=target_ids,
                    order_type=o_data.get("order_type", "move"),
                    original_text=o_data.get("original_text", ""),
                    status=OrderStatus.pending,
                    issued_at=datetime.now(timezone.utc),
                )
                db.add(order)
                await db.flush()

                snap = await _run_llm_pipeline(
                    order, self._session_id, db,
                    o_data.get("issued_by_side", "blue")
                )
                if snap and result:
                    snap.target_unit_names = o_data.get("target_unit_names", [])
                    snap.inject_tick = current_tick
                    snap.expected_classification = o_data.get("expected_classification")
                    snap.expected_order_type = o_data.get("expected_order_type")
                    snap.expected_language = o_data.get("expected_language")
                    snap.expected_locations = o_data.get("expected_locations", [])
                    snap.expected_model_tier = o_data.get("expected_model_tier")
                    result.order_snapshots.append(snap)
            else:
                # Pre-parsed order (existing behavior)
                order = Order(
                    session_id=self._session_id,
                    issued_by_side=o_data.get("issued_by_side", "blue"),
                    target_unit_ids=target_ids,
                    order_type=o_data["order_type"],
                    original_text=o_data.get("original_text", ""),
                    parsed_order=o_data.get("parsed_order"),
                    parsed_intent=o_data.get("parsed_intent"),
                    status=OrderStatus.pending,
                    issued_at=datetime.now(timezone.utc),
                )
                db.add(order)

    async def _collect_snapshot(
        self, db: AsyncSession, tick_num: int, tick_result: dict
    ) -> TickSnapshot:
        """Collect current state into a TickSnapshot."""
        # Get session time
        result = await db.execute(
            select(Session).where(Session.id == self._session_id)
        )
        session = result.scalar_one_or_none()
        game_time = session.current_time.isoformat() if session and session.current_time else None

        # Get all units
        result = await db.execute(
            select(Unit).where(Unit.session_id == self._session_id)
        )
        units_orm = list(result.scalars().all())

        unit_snapshots = []
        for u in units_orm:
            try:
                pt = to_shape(u.position)
                lat, lon = pt.y, pt.x
            except Exception:
                lat, lon = 0.0, 0.0

            unit_snapshots.append(UnitSnapshot(
                id=str(u.id),
                name=u.name,
                side=u.side.value if hasattr(u.side, 'value') else str(u.side),
                unit_type=u.unit_type,
                lat=lat,
                lon=lon,
                strength=u.strength or 0.0,
                ammo=u.ammo or 0.0,
                morale=u.morale or 0.0,
                suppression=u.suppression or 0.0,
                is_destroyed=u.is_destroyed,
                current_task=u.current_task,
                heading_deg=u.heading_deg or 0.0,
                comms_status=u.comms_status.value if hasattr(u.comms_status, 'value') else str(u.comms_status or "operational"),
            ))

        # Get contacts
        result = await db.execute(
            select(Contact).where(Contact.session_id == self._session_id)
        )
        contacts = []
        for c in result.scalars().all():
            try:
                cp = to_shape(c.location_estimate)
                c_lat, c_lon = cp.y, cp.x
            except Exception:
                c_lat, c_lon = 0.0, 0.0
            contacts.append({
                "observing_side": c.observing_side.value if hasattr(c.observing_side, 'value') else str(c.observing_side),
                "estimated_type": c.estimated_type,
                "lat": c_lat,
                "lon": c_lon,
                "confidence": c.confidence,
                "is_stale": c.is_stale,
                "last_seen_tick": c.last_seen_tick,
            })

        # Extract events from tick result
        raw_events = tick_result.get("_raw_events", [])
        radio_msgs = tick_result.get("radio_messages", [])
        reports = tick_result.get("reports", [])

        return TickSnapshot(
            tick=tick_num,
            game_time=game_time,
            units=unit_snapshots,
            events=raw_events,
            contacts=contacts,
            radio_messages=radio_msgs,
            reports=reports,
            tick_result={
                k: v for k, v in tick_result.items()
                if k not in ("_raw_events", "_smoke_updated")
            },
        )

    async def _cleanup(self, db: AsyncSession):
        """Remove all data created by this scenario."""
        if not self._session_id:
            return

        sid = self._session_id
        # Delete in dependency order
        for model in [ChatMessage, Report, Event, LocationReference, Contact, Order,
                      PlanningOverlay, MapObject,
                      TerrainCell, ElevationCell, RedAgent, GridDefinition, Unit]:
            try:
                await db.execute(
                    delete(model).where(model.session_id == sid)
                )
            except Exception:
                pass

        # Delete session and scenario
        try:
            await db.execute(delete(Session).where(Session.id == sid))
        except Exception:
            pass
        if self._scenario_id:
            try:
                await db.execute(delete(Scenario).where(Scenario.id == self._scenario_id))
            except Exception:
                pass

        # Clear terrain cache
        clear_terrain_cache()










