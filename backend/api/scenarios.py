"""Scenarios API – list, get, create scenarios."""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.database import get_db
from backend.models.scenario import Scenario

router = APIRouter()


class ScenarioCreate(BaseModel):
    title: str
    description: str | None = None
    map_center_lat: float | None = None
    map_center_lon: float | None = None
    map_zoom: int = 12
    terrain_meta: dict | None = None
    objectives: dict | None = None
    environment: dict | None = None
    grid_settings: dict | None = None
    initial_units: dict | None = None


class ScenarioRead(BaseModel):
    id: str
    title: str
    description: str | None
    map_zoom: int
    grid_settings: dict | None
    created_at: datetime


@router.get("", response_model=list[ScenarioRead])
async def list_scenarios(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Scenario))
    scenarios = result.scalars().all()
    return [
        ScenarioRead(
            id=str(s.id),
            title=s.title,
            description=s.description,
            map_zoom=s.map_zoom,
            grid_settings=s.grid_settings,
            created_at=s.created_at,
        )
        for s in scenarios
    ]


@router.get("/{scenario_id}", response_model=ScenarioRead)
async def get_scenario(scenario_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    return ScenarioRead(
        id=str(s.id),
        title=s.title,
        description=s.description,
        map_zoom=s.map_zoom,
        grid_settings=s.grid_settings,
        created_at=s.created_at,
    )


@router.post("", response_model=ScenarioRead)
async def create_scenario(body: ScenarioCreate, db: AsyncSession = Depends(get_db)):
    map_center = None
    if body.map_center_lat is not None and body.map_center_lon is not None:
        from geoalchemy2.shape import from_shape
        from shapely.geometry import Point
        map_center = from_shape(Point(body.map_center_lon, body.map_center_lat), srid=4326)

    scenario = Scenario(
        title=body.title,
        description=body.description,
        map_center=map_center,
        map_zoom=body.map_zoom,
        terrain_meta=body.terrain_meta,
        objectives=body.objectives,
        environment=body.environment,
        grid_settings=body.grid_settings,
        initial_units=body.initial_units,
    )
    db.add(scenario)
    await db.flush()
    return ScenarioRead(
        id=str(scenario.id),
        title=scenario.title,
        description=scenario.description,
        map_zoom=scenario.map_zoom,
        grid_settings=scenario.grid_settings,
        created_at=scenario.created_at,
    )


@router.delete("/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: uuid.UUID, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    s = result.scalar_one_or_none()
    if s is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await db.delete(s)
    await db.flush()


