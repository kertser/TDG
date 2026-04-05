"""
Overlay service – CRUD for planning overlays with WebSocket broadcast.
"""

from __future__ import annotations

import uuid

from geoalchemy2.shape import from_shape, to_shape
from shapely.geometry import shape, mapping
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.overlay import PlanningOverlay
from backend.services.ws_manager import ws_manager


def _serialize_overlay(overlay: PlanningOverlay) -> dict:
    """Serialize overlay ORM object to dict with GeoJSON geometry."""
    geom_geojson = None
    if overlay.geometry is not None:
        try:
            geom_geojson = mapping(to_shape(overlay.geometry))
        except Exception:
            pass

    return {
        "id": str(overlay.id),
        "session_id": str(overlay.session_id),
        "author_user_id": str(overlay.author_user_id),
        "side": overlay.side.value,
        "overlay_type": overlay.overlay_type.value,
        "geometry": geom_geojson,
        "style_json": overlay.style_json,
        "label": overlay.label,
        "properties": overlay.properties,
        "created_at": overlay.created_at.isoformat() if overlay.created_at else None,
        "updated_at": overlay.updated_at.isoformat() if overlay.updated_at else None,
    }


async def create_overlay(
    session_id: uuid.UUID,
    user_id: uuid.UUID,
    side: str,
    overlay_type: str,
    geometry: dict,
    style_json: dict | None = None,
    label: str | None = None,
    properties: dict | None = None,
    db: AsyncSession = None,
) -> dict:
    """Create a planning overlay, persist, and broadcast."""
    geom = from_shape(shape(geometry), srid=4326)

    overlay = PlanningOverlay(
        session_id=session_id,
        author_user_id=user_id,
        side=side,
        overlay_type=overlay_type,
        geometry=geom,
        style_json=style_json,
        label=label,
        properties=properties,
    )
    db.add(overlay)
    await db.flush()
    # Refresh to get computed defaults
    await db.refresh(overlay)

    serialized = _serialize_overlay(overlay)

    # Broadcast to session
    await ws_manager.broadcast(
        session_id,
        {"type": "overlay_created", "data": serialized},
    )

    return serialized


async def update_overlay(
    session_id: uuid.UUID,
    overlay_id: uuid.UUID,
    geometry: dict | None = None,
    style_json: dict | None = None,
    label: str | None = None,
    properties: dict | None = None,
    db: AsyncSession = None,
) -> dict | None:
    """Update an existing overlay, persist, and broadcast."""
    result = await db.execute(
        select(PlanningOverlay).where(
            PlanningOverlay.id == overlay_id,
            PlanningOverlay.session_id == session_id,
        )
    )
    overlay = result.scalar_one_or_none()
    if overlay is None:
        return None

    if geometry is not None:
        overlay.geometry = from_shape(shape(geometry), srid=4326)
    if style_json is not None:
        overlay.style_json = style_json
    if label is not None:
        overlay.label = label
    if properties is not None:
        overlay.properties = properties

    await db.flush()
    await db.refresh(overlay)

    serialized = _serialize_overlay(overlay)

    await ws_manager.broadcast(
        session_id,
        {"type": "overlay_updated", "data": serialized},
    )

    return serialized


async def delete_overlay(
    session_id: uuid.UUID,
    overlay_id: uuid.UUID,
    db: AsyncSession = None,
) -> bool:
    """Delete an overlay and broadcast."""
    result = await db.execute(
        select(PlanningOverlay).where(
            PlanningOverlay.id == overlay_id,
            PlanningOverlay.session_id == session_id,
        )
    )
    overlay = result.scalar_one_or_none()
    if overlay is None:
        return False

    await db.delete(overlay)
    await db.flush()

    await ws_manager.broadcast(
        session_id,
        {"type": "overlay_deleted", "data": {"overlay_id": str(overlay_id)}},
    )

    return True


async def list_overlays(
    session_id: uuid.UUID,
    side: str | None = None,
    db: AsyncSession = None,
) -> list[dict]:
    """List all overlays for a session, optionally filtered by side."""
    query = select(PlanningOverlay).where(PlanningOverlay.session_id == session_id)
    if side and side not in ("admin", "observer"):
        query = query.where(PlanningOverlay.side.in_([side, "observer"]))

    result = await db.execute(query)
    return [_serialize_overlay(o) for o in result.scalars().all()]

