"""Grid API endpoints – grid GeoJSON, snail-to-geometry, point-to-snail, viewport."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import Depends

from backend.database import get_db
from backend.models.grid import GridDefinition

router = APIRouter()


async def _get_grid_service(session_id: uuid.UUID, db: AsyncSession):
    """Helper: load GridDefinition and return GridService instance."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="Grid not defined for this session")
    from backend.services.grid_service import GridService
    return GridService(grid_def)


@router.get("/{session_id}/grid")
async def get_grid(
    session_id: uuid.UUID,
    depth: int = Query(0, ge=0, le=3),
    db: AsyncSession = Depends(get_db),
):
    """Return grid as GeoJSON FeatureCollection at given depth."""
    svc = await _get_grid_service(session_id, db)
    return svc.grid_as_geojson(depth=depth)


@router.get("/{session_id}/grid/meta")
async def get_grid_meta(
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    """Return grid metadata (dimensions, square size, etc.) for frontend config."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="Grid not defined for this session")

    from geoalchemy2.shape import to_shape
    try:
        origin = to_shape(grid_def.origin)
        origin_lat, origin_lon = origin.y, origin.x
    except Exception:
        origin_lat, origin_lon = 0, 0

    return {
        "columns": grid_def.columns,
        "rows": grid_def.rows,
        "base_square_size_m": grid_def.base_square_size_m,
        "orientation_deg": grid_def.orientation_deg,
        "labeling_scheme": grid_def.labeling_scheme,
        "max_depth": grid_def.max_depth,
        "origin_lat": origin_lat,
        "origin_lon": origin_lon,
    }


@router.get("/{session_id}/grid/viewport")
async def get_grid_viewport(
    session_id: uuid.UUID,
    south: float = Query(..., description="South latitude of viewport"),
    west: float = Query(..., description="West longitude of viewport"),
    north: float = Query(..., description="North latitude of viewport"),
    east: float = Query(..., description="East longitude of viewport"),
    depth: int = Query(1, ge=1, le=3),
    db: AsyncSession = Depends(get_db),
):
    """
    Return sub-squares for top-level squares that overlap the viewport bounds.
    Used for zoom-adaptive grid rendering — only loads sub-squares in view.
    """
    svc = await _get_grid_service(session_id, db)
    return svc.grid_viewport_geojson(south, west, north, east, depth)


@router.get("/{session_id}/grid/subdivide")
async def subdivide_square(
    session_id: uuid.UUID,
    path: str = Query(..., description="Snail path to subdivide, e.g. 'B4' or 'B4-3'"),
    db: AsyncSession = Depends(get_db),
):
    """Return the 9 children of a square/snail path."""
    svc = await _get_grid_service(session_id, db)
    if not svc.validate_snail(path):
        raise HTTPException(status_code=400, detail=f"Invalid snail path: {path}")

    from shapely.geometry import mapping
    children = svc.subdivide(path)
    features = []
    for child_path, poly in children:
        center = poly.centroid
        # Extract just the last digit for sub-square label
        parts = child_path.split("-")
        short_label = parts[-1] if len(parts) > 1 else child_path
        features.append({
            "type": "Feature",
            "properties": {
                "label": child_path,
                "short_label": short_label,
                "depth": len(parts) - 1 if svc._labeling == "alphanumeric" else len(parts),
                "center_lat": center.y,
                "center_lon": center.x,
            },
            "geometry": mapping(poly),
        })
    return {"type": "FeatureCollection", "features": features}


@router.get("/{session_id}/grid/snail-to-geometry")
async def snail_to_geometry(
    session_id: uuid.UUID,
    path: str = Query(..., description="Snail path, e.g. 'B4-3-7'"),
    db: AsyncSession = Depends(get_db),
):
    """Resolve a snail path to a GeoJSON Feature (polygon)."""
    svc = await _get_grid_service(session_id, db)
    if not svc.validate_snail(path):
        raise HTTPException(status_code=400, detail=f"Invalid snail path: {path}")

    polygon = svc.snail_to_polygon(path)
    from shapely.geometry import mapping
    return {
        "type": "Feature",
        "properties": {"snail_path": path},
        "geometry": mapping(polygon),
    }


@router.get("/{session_id}/grid/point-to-snail")
async def point_to_snail(
    session_id: uuid.UUID,
    lat: float = Query(...),
    lon: float = Query(...),
    depth: int = Query(3, ge=0, le=3),
    db: AsyncSession = Depends(get_db),
):
    """Convert geographic point to snail address. Returns null fields if point is outside grid."""
    svc = await _get_grid_service(session_id, db)

    snail_path = svc.point_to_snail(lat, lon, depth=depth)
    if snail_path is None:
        # Return a graceful empty response instead of 400 error
        return {"snail_path": None, "geometry": None, "center": None}

    polygon = svc.snail_to_polygon(snail_path)
    center = polygon.centroid
    from shapely.geometry import mapping
    return {
        "snail_path": snail_path,
        "geometry": mapping(polygon),
        "center": {"lat": center.y, "lon": center.x},
    }
