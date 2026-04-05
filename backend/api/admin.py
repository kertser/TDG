"""Admin API endpoints – DB stats, scenario deletion, etc."""

from __future__ import annotations

import uuid
from fastapi import APIRouter, HTTPException
from sqlalchemy import select, func, delete as sa_delete

from backend.api.deps import DB, CurrentUser
from backend.models.session import Session, SessionParticipant
from backend.models.scenario import Scenario
from backend.models.unit import Unit
from backend.models.order import Order, LocationReference
from backend.models.overlay import PlanningOverlay
from backend.models.contact import Contact
from backend.models.event import Event
from backend.models.report import Report
from backend.models.red_agent import RedAgent
from backend.models.grid import GridDefinition

router = APIRouter()


@router.get("/stats")
async def db_stats(db: DB, user: CurrentUser):
    """Return row counts for all major tables."""
    tables = {
        "sessions": Session,
        "participants": SessionParticipant,
        "scenarios": Scenario,
        "units": Unit,
        "orders": Order,
        "location_references": LocationReference,
        "overlays": PlanningOverlay,
        "contacts": Contact,
        "events": Event,
        "reports": Report,
        "red_agents": RedAgent,
        "grid_definitions": GridDefinition,
    }
    stats = {}
    for name, model in tables.items():
        try:
            result = await db.execute(select(func.count()).select_from(model))
            stats[name] = result.scalar() or 0
        except Exception:
            stats[name] = -1
    return stats


@router.delete("/scenarios/{scenario_id}", status_code=204)
async def delete_scenario(scenario_id: uuid.UUID, db: DB, user: CurrentUser):
    """Delete a scenario and all sessions associated with it."""
    # First delete all sessions for this scenario (cascading children)
    result = await db.execute(
        select(Session.id).where(Session.scenario_id == scenario_id)
    )
    session_ids = [row[0] for row in result.all()]

    if session_ids:
        await db.execute(sa_delete(LocationReference).where(LocationReference.session_id.in_(session_ids)))
        await db.execute(sa_delete(Event).where(Event.session_id.in_(session_ids)))
        await db.execute(sa_delete(Report).where(Report.session_id.in_(session_ids)))
        await db.execute(sa_delete(Contact).where(Contact.session_id.in_(session_ids)))
        await db.execute(sa_delete(Order).where(Order.session_id.in_(session_ids)))
        await db.execute(sa_delete(PlanningOverlay).where(PlanningOverlay.session_id.in_(session_ids)))
        await db.execute(sa_delete(RedAgent).where(RedAgent.session_id.in_(session_ids)))
        await db.execute(sa_delete(Unit).where(Unit.session_id.in_(session_ids)))
        await db.execute(sa_delete(GridDefinition).where(GridDefinition.session_id.in_(session_ids)))
        await db.execute(sa_delete(SessionParticipant).where(SessionParticipant.session_id.in_(session_ids)))
        await db.execute(sa_delete(Session).where(Session.scenario_id == scenario_id))

    # Delete the scenario itself
    result = await db.execute(select(Scenario).where(Scenario.id == scenario_id))
    scenario = result.scalar_one_or_none()
    if scenario is None:
        raise HTTPException(status_code=404, detail="Scenario not found")
    await db.delete(scenario)
    await db.flush()

