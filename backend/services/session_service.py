"""
Session service – business logic for session lifecycle.

Handles copying scenario data (units, grid, red agents) into the session
when it starts, and other session management operations.
"""

from __future__ import annotations

import math
import uuid

from geoalchemy2.shape import from_shape
from shapely.geometry import Point
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.session import Session
from backend.models.scenario import Scenario
from backend.models.unit import Unit
from backend.models.grid import GridDefinition
from backend.models.red_agent import RedAgent


def _resolve_unit_position(unit_data: dict, grid_settings: dict | None) -> Point | None:
    """
    Resolve unit position.  If the unit has grid-relative offsets
    (grid_offset_x, grid_offset_y in meters), compute absolute lat/lon
    from the session's grid origin.  Falls back to raw lat/lon.
    """
    gs = grid_settings or {}
    offset_x = unit_data.get("grid_offset_x")
    offset_y = unit_data.get("grid_offset_y")

    if offset_x is not None and offset_y is not None and gs:
        origin_lat = float(gs.get("origin_lat", 0))
        origin_lon = float(gs.get("origin_lon", 0))
        lat_rad = math.radians(origin_lat)
        m_per_deg_lat = 111320.0
        m_per_deg_lon = 111320.0 * math.cos(lat_rad) if lat_rad != 0 else 111320.0
        unit_lat = origin_lat + float(offset_y) / m_per_deg_lat
        unit_lon = origin_lon + float(offset_x) / m_per_deg_lon
        return Point(unit_lon, unit_lat)

    if "lat" in unit_data and "lon" in unit_data:
        return Point(unit_data["lon"], unit_data["lat"])

    return None


async def initialize_session_from_scenario(
    session: Session,
    scenario: Scenario,
    db: AsyncSession,
) -> None:
    """
    Copy scenario initial_units and grid_settings into the session.
    Called when a session transitions to 'running' for the first time.

    Idempotent: skips if units already exist for this session.
    """
    # Check if already initialized (units exist)
    existing = await db.execute(
        select(Unit.id).where(Unit.session_id == session.id).limit(1)
    )
    if existing.scalar_one_or_none() is not None:
        return  # Already initialized

    # Create grid definition if not present
    existing_grid = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session.id)
    )
    if existing_grid.scalar_one_or_none() is None and scenario.grid_settings:
        gs = scenario.grid_settings
        grid_def = GridDefinition(
            session_id=session.id,
            origin=from_shape(
                Point(gs.get("origin_lon", 0), gs.get("origin_lat", 0)),
                srid=4326,
            ),
            orientation_deg=gs.get("orientation_deg", 0),
            base_square_size_m=gs.get("base_square_size_m", 1000),
            columns=gs.get("columns", 8),
            rows=gs.get("rows", 8),
            labeling_scheme=gs.get("labeling_scheme", "alphanumeric"),
        )
        db.add(grid_def)

    # Create units from scenario initial_units
    if scenario.initial_units:
        unit_name_to_id: dict[str, uuid.UUID] = {}
        gs = scenario.grid_settings  # grid settings for position resolution

        for side_name in ("blue", "red"):
            for unit_data in scenario.initial_units.get(side_name, []):
                position_point = _resolve_unit_position(unit_data, gs)
                unit = Unit(
                    session_id=session.id,
                    side=side_name,
                    name=unit_data["name"],
                    unit_type=unit_data["unit_type"],
                    sidc=unit_data.get("sidc", ""),
                    position=from_shape(position_point, srid=4326)
                    if position_point else None,
                    strength=unit_data.get("strength", 1.0),
                    ammo=unit_data.get("ammo", 1.0),
                    morale=unit_data.get("morale", 0.9),
                    move_speed_mps=unit_data.get("move_speed_mps", 4.0),
                    detection_range_m=unit_data.get("detection_range_m", 1500),
                    capabilities=unit_data.get("capabilities"),
                )
                db.add(unit)
                await db.flush()
                unit_name_to_id[unit_data["name"]] = unit.id

        # Create Red Agents
        for ra_data in scenario.initial_units.get("red_agents", []):
            controlled_ids = []
            for unit_name in ra_data.get("controlled_units", []):
                uid = unit_name_to_id.get(unit_name)
                if uid:
                    controlled_ids.append(uid)

            red_agent = RedAgent(
                session_id=session.id,
                name=ra_data["name"],
                doctrine_profile=ra_data.get("doctrine_profile"),
                mission_intent=ra_data.get("mission_intent"),
                risk_posture=ra_data.get("risk_posture", "balanced"),
                controlled_unit_ids=controlled_ids if controlled_ids else None,
            )
            db.add(red_agent)

    await db.flush()


