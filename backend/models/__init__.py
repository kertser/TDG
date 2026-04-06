"""Import all ORM models so Alembic and Base.metadata can discover them."""

from backend.models.user import User
from backend.models.scenario import Scenario
from backend.models.session import Session, SessionParticipant, SessionStatus, Side
from backend.models.unit import Unit, UnitSide, CommsStatus
from backend.models.order import Order, LocationReference, OrderStatus, OrderSide, ReferenceType
from backend.models.overlay import PlanningOverlay, OverlayType, OverlaySide
from backend.models.contact import Contact, ContactSide
from backend.models.event import Event, EventVisibility
from backend.models.report import Report, ReportSide
from backend.models.red_agent import RedAgent, RiskPosture
from backend.models.grid import GridDefinition
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell

__all__ = [
    "User",
    "Scenario",
    "Session", "SessionParticipant", "SessionStatus", "Side",
    "Unit", "UnitSide", "CommsStatus",
    "Order", "LocationReference", "OrderStatus", "OrderSide", "ReferenceType",
    "PlanningOverlay", "OverlayType", "OverlaySide",
    "Contact", "ContactSide",
    "Event", "EventVisibility",
    "Report", "ReportSide",
    "RedAgent", "RiskPosture",
    "GridDefinition",
    "TerrainCell",
    "ElevationCell",
]

