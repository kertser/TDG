"""
Seed script: create a sample scenario with units, grid, and objectives.

Usage:
    python -m scripts.seed_scenario
"""

import asyncio
import uuid
from datetime import datetime, timezone

from sqlalchemy import text
from geoalchemy2.shape import from_shape
from shapely.geometry import Point

from backend.database import engine, async_session_factory, Base
from backend.models import *  # noqa: F401, F403


async def seed():
    # Create all tables (for dev; in production use Alembic)
    async with engine.begin() as conn:
        # Enable PostGIS extension
        await conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        await conn.run_sync(Base.metadata.create_all)

    async with async_session_factory() as db:
        # ── Create scenario ───────────────────────────
        scenario = Scenario(
            title="Training Exercise Alpha",
            description=(
                "A fictional combined-arms training exercise near the Reims area. "
                "Blue force (reinforced company) must secure three objectives against "
                "a defending Red force (reinforced platoon with AT assets)."
            ),
            map_center=from_shape(Point(4.49547, 49.0582), srid=4326),
            map_zoom=13,
            terrain_meta={
                "regions": [
                    {"type": "urban", "description": "Town area", "bounds": [4.48, 49.050, 4.51, 49.060]},
                    {"type": "forest", "description": "Northern woods", "bounds": [4.46, 49.060, 4.53, 49.075]},
                    {"type": "open", "description": "Southern fields", "bounds": [4.46, 49.030, 4.53, 49.050]},
                ]
            },
            objectives={
                "objectives": [
                    {"id": "OBJ_ALPHA", "name": "Objective Alpha", "lat": 49.055, "lon": 4.490, "description": "Secure crossroads"},
                    {"id": "OBJ_BRAVO", "name": "Objective Bravo", "lat": 49.062, "lon": 4.505, "description": "Secure bridge"},
                    {"id": "OBJ_CHARLIE", "name": "Objective Charlie", "lat": 49.048, "lon": 4.480, "description": "Secure hilltop"},
                ]
            },
            environment={
                "weather": "clear",
                "visibility_km": 5.0,
                "time_of_day": "morning",
                "temperature_c": 15,
            },
            grid_settings={
                "origin_lat": 49.025,
                "origin_lon": 4.440,
                "orientation_deg": 0,
                "base_square_size_m": 1000,
                "columns": 8,
                "rows": 8,
                "labeling_scheme": "alphanumeric",
            },
            initial_units={
                "blue": [
                    {
                        "name": "1st Platoon, A Company",
                        "unit_type": "infantry_platoon",
                        "sidc": "10031000151211000000",
                        "lat": 49.035, "lon": 4.465,
                        "strength": 1.0, "ammo": 1.0, "morale": 0.9,
                        "move_speed_mps": 4.0, "detection_range_m": 1500,
                        "capabilities": {"has_atgm": False, "has_mortar": False},
                    },
                    {
                        "name": "2nd Platoon, A Company",
                        "unit_type": "infantry_platoon",
                        "sidc": "10031000151211000000",
                        "lat": 49.035, "lon": 4.475,
                        "strength": 1.0, "ammo": 1.0, "morale": 0.9,
                        "move_speed_mps": 4.0, "detection_range_m": 1500,
                        "capabilities": {"has_atgm": False, "has_mortar": False},
                    },
                    {
                        "name": "3rd Platoon, A Company",
                        "unit_type": "infantry_platoon",
                        "sidc": "10031000151211000000",
                        "lat": 49.033, "lon": 4.470,
                        "strength": 1.0, "ammo": 1.0, "morale": 0.9,
                        "move_speed_mps": 4.0, "detection_range_m": 1500,
                        "capabilities": {"has_atgm": False, "has_mortar": False},
                    },
                    {
                        "name": "Mortar Section",
                        "unit_type": "mortar_section",
                        "sidc": "10031000151215000000",
                        "lat": 49.032, "lon": 4.468,
                        "strength": 1.0, "ammo": 1.0, "morale": 0.85,
                        "move_speed_mps": 3.0, "detection_range_m": 1000,
                        "capabilities": {"has_mortar": True, "mortar_range_m": 4000},
                    },
                    {
                        "name": "Recon Team",
                        "unit_type": "recon_team",
                        "sidc": "10031000151213000000",
                        "lat": 49.038, "lon": 4.472,
                        "strength": 1.0, "ammo": 0.8, "morale": 0.95,
                        "move_speed_mps": 5.0, "detection_range_m": 3000,
                        "capabilities": {"is_recon": True},
                    },
                ],
                "red": [
                    {
                        "name": "1st Red Platoon",
                        "unit_type": "infantry_platoon",
                        "sidc": "10061000151211000000",
                        "lat": 49.055, "lon": 4.490,
                        "strength": 1.0, "ammo": 1.0, "morale": 0.8,
                        "move_speed_mps": 4.0, "detection_range_m": 1500,
                        "capabilities": {"has_atgm": False},
                    },
                    {
                        "name": "Red AT Group",
                        "unit_type": "at_team",
                        "sidc": "10061000151211004000",
                        "lat": 49.060, "lon": 4.500,
                        "strength": 1.0, "ammo": 0.9, "morale": 0.75,
                        "move_speed_mps": 3.5, "detection_range_m": 2000,
                        "capabilities": {"has_atgm": True, "atgm_range_m": 3000},
                    },
                    {
                        "name": "Red Observation Post",
                        "unit_type": "observation_post",
                        "sidc": "10061000151213000000",
                        "lat": 49.065, "lon": 4.485,
                        "strength": 0.5, "ammo": 0.5, "morale": 0.7,
                        "move_speed_mps": 5.0, "detection_range_m": 4000,
                        "capabilities": {"is_recon": True},
                    },
                ],
                "red_agents": [
                    {
                        "name": "Red Company Commander",
                        "doctrine_profile": {
                            "aggression": 0.4,
                            "caution": 0.7,
                            "initiative": 0.5,
                        },
                        "mission_intent": {
                            "objective": "Defend assigned sector, delay Blue advance",
                            "constraints": ["Do not withdraw beyond phase line Bravo", "Preserve AT assets"],
                        },
                        "risk_posture": "cautious",
                        "controlled_units": ["1st Red Platoon", "Red AT Group", "Red Observation Post"],
                    }
                ],
            },
        )
        db.add(scenario)
        await db.flush()
        print(f"✅ Scenario created: {scenario.id} – {scenario.title}")

        # ── Create a demo session ─────────────────────
        session = Session(
            scenario_id=scenario.id,
            status=SessionStatus.lobby,
            tick=0,
            tick_interval=60,
            current_time=datetime.now(timezone.utc),
        )
        db.add(session)
        await db.flush()

        # ── Create grid definition ────────────────────
        gs = scenario.grid_settings
        grid_def = GridDefinition(
            session_id=session.id,
            origin=from_shape(Point(gs["origin_lon"], gs["origin_lat"]), srid=4326),
            orientation_deg=gs.get("orientation_deg", 0),
            base_square_size_m=gs.get("base_square_size_m", 1000),
            columns=gs.get("columns", 8),
            rows=gs.get("rows", 8),
            labeling_scheme=gs.get("labeling_scheme", "alphanumeric"),
        )
        db.add(grid_def)

        # ── Create units ──────────────────────────────
        for side_name in ("blue", "red"):
            for unit_data in scenario.initial_units.get(side_name, []):
                unit = Unit(
                    session_id=session.id,
                    side=side_name,
                    name=unit_data["name"],
                    unit_type=unit_data["unit_type"],
                    sidc=unit_data.get("sidc", ""),
                    position=from_shape(Point(unit_data["lon"], unit_data["lat"]), srid=4326),
                    strength=unit_data.get("strength", 1.0),
                    ammo=unit_data.get("ammo", 1.0),
                    morale=unit_data.get("morale", 0.9),
                    move_speed_mps=unit_data.get("move_speed_mps", 4.0),
                    detection_range_m=unit_data.get("detection_range_m", 1500),
                    capabilities=unit_data.get("capabilities"),
                )
                db.add(unit)

        # ── Create Red Agent ──────────────────────────
        for ra_data in scenario.initial_units.get("red_agents", []):
            red_agent = RedAgent(
                session_id=session.id,
                name=ra_data["name"],
                doctrine_profile=ra_data.get("doctrine_profile"),
                mission_intent=ra_data.get("mission_intent"),
                risk_posture=ra_data.get("risk_posture", "balanced"),
            )
            db.add(red_agent)

        await db.commit()
        print(f"✅ Session created: {session.id}")
        print(f"   Grid: {grid_def.columns}x{grid_def.rows} squares, {grid_def.base_square_size_m}m each")
        print(f"   Units: {len(scenario.initial_units.get('blue', []))} Blue, {len(scenario.initial_units.get('red', []))} Red")
        print(f"\n🚀 Ready! Start the backend with: uvicorn backend.main:app --reload")


if __name__ == "__main__":
    asyncio.run(seed())

