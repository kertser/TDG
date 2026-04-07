"""
Terrain API endpoints — analysis, query, painting, elevation.

All endpoints require the session to have a grid definition.
Analysis endpoints are admin-only (use admin password header).
"""

from __future__ import annotations

import uuid
import json
import logging
import math

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy import select, delete, func

from backend.api.deps import DB, CurrentUser
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell
from backend.models.grid import GridDefinition
from backend.engine.terrain import TERRAIN_MODIFIERS, TERRAIN_COLORS, TERRAIN_TYPES
from backend.services.grid_service import GridService

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Pydantic schemas ─────────────────────────────────────────

class AnalyzeRequest(BaseModel):
    depth: int = 3
    force: bool = False
    skip_elevation: bool = False


class PaintRequest(BaseModel):
    snail_paths: list[str]
    terrain_type: str


class TerrainPointResponse(BaseModel):
    terrain_type: str
    modifiers: dict
    elevation_m: float | None = None
    slope_deg: float | None = None
    source: str
    confidence: float


# ── Analysis endpoints ───────────────────────────────────────

@router.post("/{session_id}/terrain/analyze")
async def analyze_terrain(
    session_id: uuid.UUID,
    body: AnalyzeRequest,
    db: DB,
    current_user: CurrentUser,
):
    """
    Trigger terrain analysis for the session's grid.
    Fetches OSM + ESA WorldCover + Elevation data.
    Admin-only (any authenticated user for now — restrict later).
    """
    from backend.services.terrain_analysis.service import analyze_grid

    try:
        summary = await analyze_grid(
            session_id=session_id,
            db=db,
            depth=body.depth,
            force=body.force,
            skip_elevation=body.skip_elevation,
        )
        # Invalidate terrain cache after analysis
        from backend.engine.terrain import clear_terrain_cache
        clear_terrain_cache(str(session_id))
        return summary
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error(f"Terrain analysis failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/{session_id}/terrain/analyze-stream")
async def analyze_terrain_stream(
    session_id: uuid.UUID,
    db: DB,
    current_user: CurrentUser,
    depth: int = Query(3, ge=0, le=4),
    force: bool = Query(False),
    skip_elevation: bool = Query(False),
):
    """
    Stream terrain analysis progress via Server-Sent Events (SSE).
    Each event is a JSON object with {step, message, progress (0-1), ...}.
    Final event has step='complete' with summary data.
    """
    from backend.services.terrain_analysis.service import analyze_grid_streaming

    async def event_generator():
        try:
            async for progress in analyze_grid_streaming(
                session_id=session_id,
                db=db,
                depth=depth,
                force=force,
                skip_elevation=skip_elevation,
            ):
                yield f"data: {json.dumps(progress)}\n\n"
        except Exception as e:
            logger.error(f"Terrain analysis stream error: {e}", exc_info=True)
            yield f"data: {json.dumps({'step': 'error', 'message': str(e), 'progress': -1})}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # disable nginx buffering
        },
    )


@router.get("/{session_id}/terrain/cell-count")
async def estimate_cell_count(
    session_id: uuid.UUID,
    db: DB,
    depth: int = Query(3, ge=0, le=4),
):
    """
    Estimate the number of terrain cells that would be generated at a given depth.
    Used by the frontend to show estimated cell count before analysis.
    """
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition")

    total_squares = grid_def.columns * grid_def.rows
    rec_base = getattr(grid_def, "recursion_base", 3) or 3
    cells_per_square = (rec_base ** 2) ** depth if depth > 0 else 1
    total_cells = total_squares * cells_per_square
    cell_size_m = grid_def.base_square_size_m / (rec_base ** depth) if depth > 0 else grid_def.base_square_size_m

    return {
        "depth": depth,
        "total_squares": total_squares,
        "cells_per_square": cells_per_square,
        "total_cells": total_cells,
        "cell_size_m": round(cell_size_m, 1),
        "base_square_size_m": grid_def.base_square_size_m,
    }


# ── Query endpoints ──────────────────────────────────────────

@router.get("/{session_id}/terrain/compact")
async def get_terrain_compact(
    session_id: uuid.UUID,
    db: DB,
    depth: int | None = Query(None, description="Filter by depth"),
):
    """
    Return terrain cells as compact JSON (no polygon geometry).
    Each cell is {snail_path, lat, lon, terrain_type, source, elevation_m, ...}.
    The client reconstructs rectangles from centroids + cell_size_deg.
    ~100× faster than the GeoJSON polygon endpoint for large grids.
    """
    # Load grid for cell size computation
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition for this session")

    # Load terrain cells
    query = select(TerrainCell).where(TerrainCell.session_id == session_id)
    if depth is not None:
        query = query.where(TerrainCell.depth == depth)
    result = await db.execute(query)
    cells = result.scalars().all()

    if not cells:
        return {"cells": [], "cell_size_lat": 0, "cell_size_lon": 0, "colors": TERRAIN_COLORS}

    # Compute cell size in degrees using the grid's AEQD projection
    # (the same projection used to place centroids in enumerate_cells).
    # This avoids the ~0.8 m/cell error that the simple degree formula has.
    cell_depth = cells[0].depth if cells else 1
    rec_base = getattr(grid_def, "recursion_base", 3) or 3
    cell_size_m = grid_def.base_square_size_m / (rec_base ** cell_depth)

    grid_service = GridService(grid_def)
    # Measure one cell step at the grid center (most representative)
    cx = (grid_def.columns / 2) * grid_def.base_square_size_m
    cy = (grid_def.rows / 2) * grid_def.base_square_size_m
    _c_lon, _c_lat = grid_service._to_geo.transform(cx, cy)
    _e_lon, _e_lat = grid_service._to_geo.transform(cx + cell_size_m, cy)
    _n_lon, _n_lat = grid_service._to_geo.transform(cx, cy + cell_size_m)
    lon_deg = abs(_e_lon - _c_lon)
    lat_deg = abs(_n_lat - _c_lat)

    compact_cells = []
    for cell in cells:
        compact_cells.append({
            "s": cell.snail_path,
            "t": cell.terrain_type,
            "la": round(cell.centroid_lat, 7),
            "lo": round(cell.centroid_lon, 7),
            "e": round(cell.elevation_m, 1) if cell.elevation_m is not None else None,
            "sl": round(cell.slope_deg, 1) if cell.slope_deg is not None else None,
            "sr": cell.source,
            "c": cell.confidence,
        })

    return {
        "cells": compact_cells,
        "cell_size_lat": round(lat_deg, 8),
        "cell_size_lon": round(lon_deg, 8),
        "colors": TERRAIN_COLORS,
        "modifiers": {t: TERRAIN_MODIFIERS[t] for t in TERRAIN_TYPES if t in TERRAIN_MODIFIERS},
    }


@router.get("/{session_id}/terrain")
async def get_terrain(
    session_id: uuid.UUID,
    db: DB,
    depth: int | None = Query(None, description="Filter by depth"),
):
    """
    Return all TerrainCells as GeoJSON FeatureCollection.
    Each feature is a polygon colored by terrain type.
    """
    # Load grid for polygon generation
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition for this session")

    grid_service = GridService(grid_def)

    # Load terrain cells
    query = select(TerrainCell).where(TerrainCell.session_id == session_id)
    if depth is not None:
        query = query.where(TerrainCell.depth == depth)
    result = await db.execute(query)
    cells = result.scalars().all()

    if not cells:
        return {"type": "FeatureCollection", "features": []}

    features = []
    for cell in cells:
        try:
            poly = grid_service.snail_to_polygon(cell.snail_path)
            from shapely.geometry import mapping
            geom = mapping(poly)
        except Exception:
            # Fallback: create a small box around centroid
            d = 0.0015
            geom = {
                "type": "Polygon",
                "coordinates": [[
                    [cell.centroid_lon - d, cell.centroid_lat - d],
                    [cell.centroid_lon + d, cell.centroid_lat - d],
                    [cell.centroid_lon + d, cell.centroid_lat + d],
                    [cell.centroid_lon - d, cell.centroid_lat + d],
                    [cell.centroid_lon - d, cell.centroid_lat - d],
                ]],
            }

        features.append({
            "type": "Feature",
            "properties": {
                "snail_path": cell.snail_path,
                "terrain_type": cell.terrain_type,
                "source": cell.source,
                "confidence": cell.confidence,
                "elevation_m": cell.elevation_m,
                "slope_deg": cell.slope_deg,
                "color": TERRAIN_COLORS.get(cell.terrain_type, "#90EE90"),
                "modifiers": cell.modifiers or TERRAIN_MODIFIERS.get(cell.terrain_type, {}),
            },
            "geometry": geom,
        })

    return {"type": "FeatureCollection", "features": features}


@router.get("/{session_id}/terrain/at")
async def get_terrain_at_point(
    session_id: uuid.UUID,
    db: DB,
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
):
    """Single point terrain query."""
    # Load grid
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition")

    grid_service = GridService(grid_def)

    # Find snail path for the point
    # Try different depths
    for d in [2, 1, 0]:
        snail = grid_service.point_to_snail(lat, lon, depth=d)
        if snail:
            result = await db.execute(
                select(TerrainCell).where(
                    TerrainCell.session_id == session_id,
                    TerrainCell.snail_path == snail,
                )
            )
            cell = result.scalar_one_or_none()
            if cell:
                return {
                    "snail_path": snail,
                    "terrain_type": cell.terrain_type,
                    "modifiers": cell.modifiers or TERRAIN_MODIFIERS.get(cell.terrain_type, {}),
                    "elevation_m": cell.elevation_m,
                    "slope_deg": cell.slope_deg,
                    "source": cell.source,
                    "confidence": cell.confidence,
                }

    return {
        "snail_path": None,
        "terrain_type": "open",
        "modifiers": TERRAIN_MODIFIERS["open"],
        "elevation_m": None,
        "slope_deg": None,
        "source": "default",
        "confidence": 0.0,
    }


@router.get("/{session_id}/terrain/stats")
async def get_terrain_stats(
    session_id: uuid.UUID,
    db: DB,
):
    """Return terrain statistics: count per type, sources, etc."""
    result = await db.execute(
        select(
            TerrainCell.terrain_type,
            func.count(TerrainCell.id),
        )
        .where(TerrainCell.session_id == session_id)
        .group_by(TerrainCell.terrain_type)
    )
    type_counts = {row[0]: row[1] for row in result}

    result = await db.execute(
        select(
            TerrainCell.source,
            func.count(TerrainCell.id),
        )
        .where(TerrainCell.session_id == session_id)
        .group_by(TerrainCell.source)
    )
    source_counts = {row[0]: row[1] for row in result}

    total = sum(type_counts.values())

    return {
        "total_cells": total,
        "by_type": type_counts,
        "by_source": source_counts,
        "terrain_types": TERRAIN_TYPES,
        "terrain_colors": TERRAIN_COLORS,
    }


# ── Manual painting endpoints ────────────────────────────────

@router.patch("/{session_id}/terrain/{snail_path}")
async def paint_terrain_cell(
    session_id: uuid.UUID,
    snail_path: str,
    db: DB,
    current_user: CurrentUser,
    terrain_type: str = Query(..., description="Terrain type to set"),
):
    """
    Admin manual override for a single cell.
    Sets source='manual'. Manual cells are never overwritten by auto-analysis.
    """
    if terrain_type not in TERRAIN_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid terrain type: {terrain_type}")

    modifiers = TERRAIN_MODIFIERS.get(terrain_type, TERRAIN_MODIFIERS["open"])

    # Check if cell exists
    result = await db.execute(
        select(TerrainCell).where(
            TerrainCell.session_id == session_id,
            TerrainCell.snail_path == snail_path,
        )
    )
    cell = result.scalar_one_or_none()

    if cell:
        cell.terrain_type = terrain_type
        cell.source = "manual"
        cell.confidence = 1.0
        cell.modifiers = modifiers
    else:
        # Need to compute centroid
        grid_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        grid_def = grid_result.scalar_one_or_none()
        if grid_def is None:
            raise HTTPException(status_code=404, detail="No grid definition")

        grid_service = GridService(grid_def)
        try:
            center = grid_service.snail_to_center(snail_path)
        except Exception:
            raise HTTPException(status_code=400, detail=f"Invalid snail path: {snail_path}")

        depth = snail_path.count("-")
        cell = TerrainCell(
            session_id=session_id,
            snail_path=snail_path,
            depth=depth,
            terrain_type=terrain_type,
            modifiers=modifiers,
            source="manual",
            confidence=1.0,
            centroid_lat=center.y,
            centroid_lon=center.x,
        )
        db.add(cell)

    return {
        "snail_path": snail_path,
        "terrain_type": terrain_type,
        "source": "manual",
    }


@router.post("/{session_id}/terrain/paint")
async def batch_paint_terrain(
    session_id: uuid.UUID,
    body: PaintRequest,
    db: DB,
    current_user: CurrentUser,
):
    """
    Admin batch paint. Sets multiple cells at once. Source='manual'.
    """
    if body.terrain_type not in TERRAIN_TYPES:
        raise HTTPException(status_code=400, detail=f"Invalid terrain type: {body.terrain_type}")

    modifiers = TERRAIN_MODIFIERS.get(body.terrain_type, TERRAIN_MODIFIERS["open"])

    # Load grid for centroid computation
    grid_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = grid_result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition")

    grid_service = GridService(grid_def)

    painted = 0
    for snail_path in body.snail_paths:
        result = await db.execute(
            select(TerrainCell).where(
                TerrainCell.session_id == session_id,
                TerrainCell.snail_path == snail_path,
            )
        )
        cell = result.scalar_one_or_none()

        if cell:
            cell.terrain_type = body.terrain_type
            cell.source = "manual"
            cell.confidence = 1.0
            cell.modifiers = modifiers
        else:
            try:
                center = grid_service.snail_to_center(snail_path)
            except Exception:
                continue

            depth = snail_path.count("-")
            cell = TerrainCell(
                session_id=session_id,
                snail_path=snail_path,
                depth=depth,
                terrain_type=body.terrain_type,
                modifiers=modifiers,
                source="manual",
                confidence=1.0,
                centroid_lat=center.y,
                centroid_lon=center.x,
            )
            db.add(cell)
        painted += 1

    # Invalidate terrain cache
    from backend.engine.terrain import clear_terrain_cache
    clear_terrain_cache(str(session_id))

    return {"painted": painted, "terrain_type": body.terrain_type}


@router.delete("/{session_id}/terrain")
async def clear_terrain(
    session_id: uuid.UUID,
    db: DB,
    current_user: CurrentUser,
    keep_manual: bool = Query(True, description="Preserve manually painted cells"),
):
    """Admin-only: clear all auto-analyzed terrain cells."""
    query = delete(TerrainCell).where(TerrainCell.session_id == session_id)
    if keep_manual:
        query = query.where(TerrainCell.source != "manual")
    result = await db.execute(query)
    deleted_count = getattr(result, "rowcount", 0)

    # Also clear elevation cells
    await db.execute(
        delete(ElevationCell).where(ElevationCell.session_id == session_id)
    )

    # Invalidate terrain cache
    from backend.engine.terrain import clear_terrain_cache
    clear_terrain_cache(str(session_id))

    return {"deleted": deleted_count, "kept_manual": keep_manual}


# ── Elevation endpoints ──────────────────────────────────────

@router.get("/{session_id}/elevation")
async def get_elevation(
    session_id: uuid.UUID,
    db: DB,
):
    """Return ElevationCells as GeoJSON with elevation values."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition")

    grid_service = GridService(grid_def)

    result = await db.execute(
        select(ElevationCell).where(ElevationCell.session_id == session_id)
    )
    cells = result.scalars().all()

    if not cells:
        return {"type": "FeatureCollection", "features": []}

    # Find elevation range for color interpolation
    elevations = [c.elevation_m for c in cells]
    min_elev = min(elevations) if elevations else 0
    max_elev = max(elevations) if elevations else 100
    elev_range = max_elev - min_elev or 1

    features = []
    for cell in cells:
        try:
            poly = grid_service.snail_to_polygon(cell.snail_path)
            from shapely.geometry import mapping
            geom = mapping(poly)
        except Exception:
            d = 0.0015
            geom = {
                "type": "Polygon",
                "coordinates": [[
                    [cell.centroid_lon - d, cell.centroid_lat - d],
                    [cell.centroid_lon + d, cell.centroid_lat - d],
                    [cell.centroid_lon + d, cell.centroid_lat + d],
                    [cell.centroid_lon - d, cell.centroid_lat + d],
                    [cell.centroid_lon - d, cell.centroid_lat - d],
                ]],
            }

        # Color: green(low) → yellow → brown(high)
        t = (cell.elevation_m - min_elev) / elev_range
        r = int(139 + t * (139 - 139))  # stay brown-ish
        g = int(195 - t * 115)
        b = int(74 - t * 30)
        color = f"#{min(255,r):02x}{max(0,g):02x}{max(0,b):02x}"

        features.append({
            "type": "Feature",
            "properties": {
                "snail_path": cell.snail_path,
                "elevation_m": cell.elevation_m,
                "slope_deg": cell.slope_deg,
                "aspect_deg": cell.aspect_deg,
                "color": color,
            },
            "geometry": geom,
        })

    return {
        "type": "FeatureCollection",
        "features": features,
        "metadata": {
            "min_elevation_m": round(min_elev, 1),
            "max_elevation_m": round(max_elev, 1),
        },
    }


@router.get("/{session_id}/elevation/at")
async def get_elevation_at_point(
    session_id: uuid.UUID,
    db: DB,
    lat: float = Query(...),
    lon: float = Query(...),
):
    """Single point elevation query."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        raise HTTPException(status_code=404, detail="No grid definition")

    grid_service = GridService(grid_def)

    for d in [2, 1, 0]:
        snail = grid_service.point_to_snail(lat, lon, depth=d)
        if snail:
            result = await db.execute(
                select(ElevationCell).where(
                    ElevationCell.session_id == session_id,
                    ElevationCell.snail_path == snail,
                )
            )
            cell = result.scalar_one_or_none()
            if cell:
                return {
                    "snail_path": snail,
                    "elevation_m": cell.elevation_m,
                    "slope_deg": cell.slope_deg,
                    "aspect_deg": cell.aspect_deg,
                }

    return {"snail_path": None, "elevation_m": None, "slope_deg": None, "aspect_deg": None}


# ── Reference data endpoint ──────────────────────────────────

@router.get("/{session_id}/terrain/types")
async def get_terrain_types(session_id: uuid.UUID):
    """Return terrain type definitions, modifiers, and colors."""
    return {
        "types": TERRAIN_TYPES,
        "modifiers": TERRAIN_MODIFIERS,
        "colors": TERRAIN_COLORS,
    }


