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
import copy

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


# ── Persist pathfinding graph to DB after terrain analysis ────
async def _persist_pathfinding_graph(session_id: uuid.UUID, db):
    """Build static pathfinding graph and save to GridDefinition.settings_json.

    Called after terrain analysis completes. The graph (~200-400KB JSON for
    900 depth-1 cells) is loaded on next session start instead of rebuilding
    from scratch, saving 1-3 seconds on the first tick.
    """
    try:
        sid = str(session_id)
        # Load terrain + elevation cells
        tc_result = await db.execute(
            select(TerrainCell.snail_path, TerrainCell.terrain_type)
            .where(TerrainCell.session_id == session_id)
        )
        tc_rows = tc_result.all()
        if not tc_rows:
            return
        terrain_cells = {row[0]: row[1] for row in tc_rows}

        ec_result = await db.execute(
            select(
                ElevationCell.snail_path, ElevationCell.elevation_m,
                ElevationCell.slope_deg, ElevationCell.aspect_deg,
            ).where(ElevationCell.session_id == session_id)
        )
        ec_rows = ec_result.all()
        elevation_cells = {
            row[0]: {"elevation_m": row[1], "slope_deg": row[2], "aspect_deg": row[3]}
            for row in ec_rows
        } if ec_rows else None

        # Load grid definition
        gd_result = await db.execute(
            select(GridDefinition).where(GridDefinition.session_id == session_id)
        )
        gd = gd_result.scalar_one_or_none()
        if not gd:
            return
        grid_service = GridService(gd)

        # Build centroids + graph
        from backend.services.pathfinding_service import (
            build_static_graph, serialize_static_graph, set_cached_graph,
        )
        from backend.engine.tick import _set_cached_centroids

        cell_centroids = {}
        for path in terrain_cells:
            try:
                center = grid_service.snail_to_center(path)
                if center:
                    cell_centroids[path] = (center.y, center.x)
            except Exception:
                pass

        if not cell_centroids:
            return

        graph = build_static_graph(terrain_cells, elevation_cells, cell_centroids, grid_service)
        if not graph.get("centroids"):
            return

        # Cache in memory
        set_cached_graph(sid, graph)
        _set_cached_centroids(sid, cell_centroids)

        # Persist to DB
        serialized = serialize_static_graph(graph)
        settings = dict(gd.settings_json or {})
        settings["pathfinding_graph"] = serialized
        gd.settings_json = settings
        await db.flush()
        logger.info("Persisted pathfinding graph for session %s (%d cells)", sid, len(cell_centroids))
    except Exception as e:
        logger.warning("Failed to persist pathfinding graph: %s", e)

# ── In-memory peaks cache per session ────────────────────────
# Avoids recomputing peaks (expensive 8-ray algorithm) on every request.
# Invalidated when terrain is analyzed or cleared.
# DB-backed: results are also persisted in GridDefinition.settings_json['peaks_cache']
# so they survive server restarts and are only recomputed when terrain changes.
import time as _time
_peaks_cache: dict[str, dict] = {}  # session_id → {"data": {...}, "ts": float, "params": (prom, dist)}
_PEAKS_CACHE_TTL = 86400  # 24 hours (in-memory TTL)


def _invalidate_peaks_cache(session_id: str):
    """Invalidate the in-memory peaks cache for a session."""
    _peaks_cache.pop(session_id, None)


def _get_peaks_cache(session_id: str, prominence: float, distance: float) -> dict | None:
    entry = _peaks_cache.get(session_id)
    if not entry:
        return None
    if _time.time() - entry["ts"] > _PEAKS_CACHE_TTL:
        _peaks_cache.pop(session_id, None)
        return None
    if entry["params"] != (prominence, distance):
        return None
    return entry["data"]


def _set_peaks_cache(session_id: str, prominence: float, distance: float, data: dict):
    _peaks_cache[session_id] = {"data": data, "ts": _time.time(), "params": (prominence, distance)}


async def _load_db_peaks_cache(session_id: uuid.UUID, prominence: float, distance: float, db: DB) -> dict | None:
    """Load persisted peaks from GridDefinition.settings_json. Returns None on miss/mismatch."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = result.scalar_one_or_none()
    if not gd or not gd.settings_json:
        return None
    db_cache = gd.settings_json.get("peaks_cache")
    if not db_cache:
        return None
    stored_params = db_cache.get("params")
    if stored_params != [prominence, distance]:
        return None
    return db_cache.get("data")


async def _save_db_peaks_cache(session_id: uuid.UUID, prominence: float, distance: float, data: dict, db: DB):
    """Persist computed peaks into GridDefinition.settings_json."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = result.scalar_one_or_none()
    if gd is None:
        return
    new_settings = copy.deepcopy(gd.settings_json or {})
    new_settings["peaks_cache"] = {
        "params": [prominence, distance],
        "data": data,
        "computed_at": _time.time(),
    }
    # Reassign to trigger SQLAlchemy dirty tracking on JSONB column
    gd.settings_json = new_settings
    await db.flush()


async def _clear_db_peaks_cache(session_id: uuid.UUID, db: DB):
    """Remove persisted peaks cache from GridDefinition.settings_json."""
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = result.scalar_one_or_none()
    if gd and gd.settings_json and "peaks_cache" in gd.settings_json:
        new_settings = copy.deepcopy(gd.settings_json)
        del new_settings["peaks_cache"]
        gd.settings_json = new_settings
        await db.flush()


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
        # Invalidate terrain cache after analysis (in-memory + DB)
        from backend.engine.terrain import clear_terrain_cache
        clear_terrain_cache(str(session_id))
        from backend.engine.tick import clear_pathfinding_cache
        clear_pathfinding_cache(str(session_id))
        from backend.services.pathfinding_service import clear_graph_cache
        clear_graph_cache(str(session_id))
        _invalidate_peaks_cache(str(session_id))
        _path_cache.pop(str(session_id), None)
        await _clear_db_peaks_cache(session_id, db)
        # Pre-build and persist pathfinding graph for instant availability
        await _persist_pathfinding_graph(session_id, db)
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

    # Invalidate peaks cache at analysis start (in-memory + DB)
    _invalidate_peaks_cache(str(session_id))
    _path_cache.pop(str(session_id), None)
    await _clear_db_peaks_cache(session_id, db)

    async def event_generator():
        try:
            async for progress in analyze_grid_streaming(
                session_id=session_id,
                db=db,
                depth=depth,
                force=force,
                skip_elevation=skip_elevation,
            ):
                # Also invalidate terrain engine cache on completion
                if progress.get("step") == "complete":
                    from backend.engine.terrain import clear_terrain_cache
                    clear_terrain_cache(str(session_id))
                    from backend.engine.tick import clear_pathfinding_cache
                    clear_pathfinding_cache(str(session_id))
                    from backend.services.pathfinding_service import clear_graph_cache
                    clear_graph_cache(str(session_id))
                    # Pre-build and persist graph in background
                    try:
                        await _persist_pathfinding_graph(session_id, db)
                    except Exception:
                        pass  # best effort
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
    from backend.engine.tick import clear_pathfinding_cache
    clear_pathfinding_cache(str(session_id))
    from backend.services.pathfinding_service import clear_graph_cache
    clear_graph_cache(str(session_id))
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

    # Invalidate terrain cache (in-memory + DB peaks cache)
    from backend.engine.terrain import clear_terrain_cache
    clear_terrain_cache(str(session_id))
    from backend.engine.tick import clear_pathfinding_cache
    clear_pathfinding_cache(str(session_id))
    from backend.services.pathfinding_service import clear_graph_cache
    clear_graph_cache(str(session_id))
    _invalidate_peaks_cache(str(session_id))
    _path_cache.pop(str(session_id), None)
    await _clear_db_peaks_cache(session_id, db)

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


# ── Elevation peaks (hilltops) ────────────────────────────────

@router.get("/{session_id}/elevation/peaks")
async def get_elevation_peaks(
    session_id: uuid.UUID,
    db: DB,
    min_prominence_m: float = Query(5.0, description="Minimum height difference from surrounding terrain to count as peak"),
    min_distance_m: float = Query(1500.0, description="Minimum distance in meters between reported peaks (dedup radius)"),
):
    """
    Find dominant hilltops (height tops / высоты) — terrain features where
    the ground descends in every direction for a significant distance.

    Algorithm:
      1. For each cell, cast 8 rays outward (N, NE, E, SE, S, SW, W, NW),
         each ray extending `check_dist` cells (~2-3 km).
      2. A direction "descends" if the MAX elevation along the entire ray
         is strictly lower than the candidate cell.
      3. A cell is a peak only if ALL 8 directions (with data) descend.
      4. Dynamic prominence threshold adapts to terrain relief — ensures
         peaks are significant relative to the landscape, not just noise.
      5. Dedup: within min_distance_m, only the highest peak is kept.

    Results are persisted in GridDefinition.settings_json['peaks_cache'] so they
    survive server restarts and are only recomputed when terrain data changes.
    """
    # ── Tier 1: in-memory cache (fastest) ──
    sid = str(session_id)
    cached = _get_peaks_cache(sid, min_prominence_m, min_distance_m)
    if cached is not None:
        return cached

    # ── Tier 2: DB-persisted cache (survives restarts) ──
    db_cached = await _load_db_peaks_cache(session_id, min_prominence_m, min_distance_m, db)
    if db_cached is not None:
        logger.debug(f"Peaks cache hit (DB) for session {sid}")
        _set_peaks_cache(sid, min_prominence_m, min_distance_m, db_cached)
        return db_cached

    # ── Tier 3: Compute from ElevationCell data ──
    logger.info(f"Computing elevation peaks for session {sid} (no cache hit)")

    result = await db.execute(
        select(ElevationCell).where(ElevationCell.session_id == session_id)
    )
    cells = result.scalars().all()
    if not cells:
        return {"peaks": []}

    all_cells = [c for c in cells if c.elevation_m is not None]
    if not all_cells:
        return {"peaks": []}

    all_cells.sort(key=lambda c: c.snail_path)

    # Compute cell spacing from grid definition
    cell_size_lat = 0.001  # fallback
    cell_size_lon = 0.001
    gd_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = gd_result.scalar_one_or_none()
    if gd and len(all_cells) >= 2:
        depth = all_cells[0].depth if all_cells else 1
        rec_base = getattr(gd, "recursion_base", 3) or 3
        cell_size_m = gd.base_square_size_m / (rec_base ** depth)
        gs = GridService(gd)
        cx_local = (gd.columns / 2) * gd.base_square_size_m
        cy_local = (gd.rows / 2) * gd.base_square_size_m
        _c_lon, _c_lat = gs._to_geo.transform(cx_local, cy_local)
        _e_lon, _e_lat = gs._to_geo.transform(cx_local + cell_size_m, cy_local)
        _n_lon, _n_lat = gs._to_geo.transform(cx_local, cy_local + cell_size_m)
        cell_size_lat = abs(_n_lat - _c_lat)
        cell_size_lon = abs(_e_lon - _c_lon)

    # Build spatial grid index: "row,col" → (max_elev, min_elev)
    min_lat_val = min(c.centroid_lat for c in all_cells)
    min_lon_val = min(c.centroid_lon for c in all_cells)
    grid_idx: dict[str, list] = {}
    for c in all_cells:
        row = round((c.centroid_lat - min_lat_val) / cell_size_lat) if cell_size_lat > 0 else 0
        col = round((c.centroid_lon - min_lon_val) / cell_size_lon) if cell_size_lon > 0 else 0
        key = f"{row},{col}"
        if key not in grid_idx:
            grid_idx[key] = []
        grid_idx[key].append(c)

    _elev_cache: dict[str, tuple] = {}
    for key, cell_list in grid_idx.items():
        elevs = [c.elevation_m for c in cell_list if c.elevation_m is not None]
        if elevs:
            _elev_cache[key] = (max(elevs), min(elevs))

    # ── Compute terrain statistics for dynamic prominence threshold ──
    all_elevs = [c.elevation_m for c in all_cells]
    terrain_max = max(all_elevs)
    terrain_min = min(all_elevs)
    terrain_range = terrain_max - terrain_min

    # Dynamic prominence: scale with terrain relief.
    # For flat terrain (range ~50m), require ~10m. For mountainous (range ~500m), ~50m.
    # Formula: max(user_param, 15% of range, hard floor of 8m)
    effective_prominence = max(min_prominence_m, terrain_range * 0.15, 8.0)

    # ── 8 cardinal + diagonal directions ──
    DIRECTIONS_8 = [
        (1, 0), (1, 1), (0, 1), (-1, 1),
        (-1, 0), (-1, -1), (0, -1), (1, -1),
    ]

    # How many cells to check outward per direction.
    # Must be long enough to distinguish real hilltops from slope undulations.
    # With ~333m cells, 8 steps = ~2.7km per ray — good for typical terrain.
    check_dist = 8

    # ── Find peaks: cells with terrain descending in every direction ──
    peaks = []
    for cell in all_cells:
        row = round((cell.centroid_lat - min_lat_val) / cell_size_lat) if cell_size_lat > 0 else 0
        col = round((cell.centroid_lon - min_lon_val) / cell_size_lon) if cell_size_lon > 0 else 0
        elev = cell.elevation_m

        descending = 0
        checked = 0
        lowest_far = elev

        for dr, dc in DIRECTIONS_8:
            ray_max = None
            ray_min = None
            for step in range(1, check_dist + 1):
                key = f"{row + dr * step},{col + dc * step}"
                cached = _elev_cache.get(key)
                if cached is not None:
                    e_max, e_min = cached
                    ray_max = e_max if ray_max is None else max(ray_max, e_max)
                    ray_min = e_min if ray_min is None else min(ray_min, e_min)

            if ray_max is None:
                continue  # no data in this direction (grid edge)

            checked += 1
            if ray_max < elev:
                descending += 1
                lowest_far = min(lowest_far, ray_min)

        # Require data in at least 5 directions and ALL must descend
        if checked < 5 or descending < checked:
            continue

        # Prominence: how much this peak rises above surrounding terrain
        prominence = elev - lowest_far
        if prominence < effective_prominence:
            continue

        peaks.append({
            "snail_path": cell.snail_path,
            "lat": round(cell.centroid_lat, 7),
            "lon": round(cell.centroid_lon, 7),
            "elevation_m": round(elev, 1),
            "prominence_m": round(prominence, 1),
            "label": f"Height {round(elev)}",
            "label_ru": f"Высота {round(elev)}",
        })

    # Sort by elevation descending (highest peaks first — kept during dedup)
    peaks.sort(key=lambda p: p["elevation_m"], reverse=True)

    # Deduplicate: proper Euclidean distance check
    avg_lat = sum(p["lat"] for p in peaks) / len(peaks) if peaks else 48.0
    meters_per_deg_lat = 111320.0
    meters_per_deg_lon = 111320.0 * math.cos(math.radians(avg_lat))
    min_dist_sq = min_distance_m * min_distance_m

    deduped = []
    for peak in peaks:
        too_close = False
        for existing in deduped:
            dlat_m = (peak["lat"] - existing["lat"]) * meters_per_deg_lat
            dlon_m = (peak["lon"] - existing["lon"]) * meters_per_deg_lon
            dist_sq = dlat_m * dlat_m + dlon_m * dlon_m
            if dist_sq < min_dist_sq:
                too_close = True
                break
        if not too_close:
            deduped.append(peak)

    response_data = {"peaks": deduped}

    # Store in both in-memory cache and DB (for persistence across restarts)
    _set_peaks_cache(sid, min_prominence_m, min_distance_m, response_data)
    try:
        await _save_db_peaks_cache(session_id, min_prominence_m, min_distance_m, response_data, db)
        logger.info(f"Peaks computed and persisted for session {sid}: {len(deduped)} peaks")
    except Exception as e:
        logger.warning(f"Could not persist peaks cache to DB: {e}")

    return response_data


# ── Reference data endpoint ──────────────────────────────────

async def get_elevation_peaks_cached(
    session_id: uuid.UUID,
    db,
    min_prominence_m: float = 5.0,
    min_distance_m: float = 1500.0,
) -> list[dict]:
    """
    Shared helper to get elevation peaks using the 3-tier cache.
    Called by order_service.py and terrain API endpoint.
    Returns list of peak dicts (not the full response envelope).
    """
    sid = str(session_id)

    # Tier 1: in-memory
    cached = _get_peaks_cache(sid, min_prominence_m, min_distance_m)
    if cached is not None:
        return cached.get("peaks", [])

    # Tier 2: DB-persisted
    db_cached = await _load_db_peaks_cache(session_id, min_prominence_m, min_distance_m, db)
    if db_cached is not None:
        _set_peaks_cache(sid, min_prominence_m, min_distance_m, db_cached)
        return db_cached.get("peaks", [])

    # Tier 3: no cache → return empty (peaks will be computed on next GET /elevation/peaks)
    # We don't trigger full computation here to avoid blocking order processing.
    return []


@router.get("/{session_id}/terrain/types")
async def get_terrain_types(session_id: uuid.UUID):
    """Return terrain type definitions, modifiers, and colors."""
    return {
        "types": TERRAIN_TYPES,
        "modifiers": TERRAIN_MODIFIERS,
        "colors": TERRAIN_COLORS,
    }


# ── Pathfinding endpoint ─────────────────────────────────────

# In-memory path cache: session_id → {(from_snail, to_snail) → {path, ts}}
_path_cache: dict[str, dict] = {}
_PATH_CACHE_TTL = 300  # 5 minutes


def _get_cached_path(session_id: str, from_snail: str, to_snail: str) -> list | None:
    """Get a cached path, or None if expired/missing."""
    entries = _path_cache.get(session_id)
    if not entries:
        return None
    key = (from_snail, to_snail)
    entry = entries.get(key)
    if not entry:
        return None
    if _time.time() - entry["ts"] > _PATH_CACHE_TTL:
        entries.pop(key, None)
        return None
    return entry["path"]


def _set_cached_path(session_id: str, from_snail: str, to_snail: str, path: list):
    if session_id not in _path_cache:
        _path_cache[session_id] = {}
    _path_cache[session_id][(from_snail, to_snail)] = {"path": path, "ts": _time.time()}


@router.get("/{session_id}/pathfind")
async def find_path(
    session_id: uuid.UUID,
    db: DB,
    current_user: CurrentUser,
    from_lat: float = Query(..., description="Start latitude"),
    from_lon: float = Query(..., description="Start longitude"),
    to_lat: float = Query(..., description="End latitude"),
    to_lon: float = Query(..., description="End longitude"),
    speed_mode: str = Query("fast", description="'fast' (roads/speed) or 'slow' (cover/concealment)"),
    unit_id: str | None = Query(None, description="Unit ID — if provided, loads contacts for tactical routing"),
):
    """
    Find optimal movement path between two points using A* search.

    Returns a list of (lat, lon) waypoints that avoids water, minefields,
    and prefers roads/open terrain over forest/marsh/mountain.

    When unit_id is provided, the path also considers:
    - Known enemy positions (avoids their detection zones)
    - Friendly unit positions (prefers routes with mutual support)
    - Speed mode: 'slow' maximizes cover, 'fast' prioritizes speed

    Falls back to empty path (straight line) if no terrain data or path not found.
    """
    sid = str(session_id)
    if speed_mode not in ("fast", "slow"):
        speed_mode = "fast"

    # Load grid definition
    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        return {"path": [], "cost": 0, "cells": 0}

    grid_service = GridService(grid_def)

    # Snap to snail paths for cache key (include speed_mode)
    from_snail = grid_service.point_to_snail(from_lat, from_lon, depth=1) or ""
    to_snail = grid_service.point_to_snail(to_lat, to_lon, depth=1) or ""
    cache_key_snail = f"{from_snail}_{speed_mode}" if from_snail else ""

    # Check path cache
    if cache_key_snail and to_snail:
        cached_path = _get_cached_path(sid, cache_key_snail, to_snail)
        if cached_path is not None:
            return {"path": cached_path, "cost": 0, "cells": len(cached_path), "cached": True}

    # Load terrain data (use session cache if available)
    from backend.engine.terrain import get_cached_terrain_data, set_cached_terrain_data
    cached = get_cached_terrain_data(sid)

    if cached:
        terrain_cells = cached.get("terrain_cells") or {}
        elevation_cells = cached.get("elevation_cells") or {}
        cell_centroids = cached.get("cell_centroids") or {}
    else:
        # Load from DB
        result_tc = await db.execute(
            select(TerrainCell).where(TerrainCell.session_id == session_id)
        )
        cells = result_tc.scalars().all()
        if not cells:
            return {"path": [], "cost": 0, "cells": 0}

        terrain_cells = {c.snail_path: c.terrain_type for c in cells}
        cell_centroids = {c.snail_path: (c.centroid_lat, c.centroid_lon) for c in cells}
        elev_result = await db.execute(
            select(ElevationCell).where(ElevationCell.session_id == session_id)
        )
        elev_cells = elev_result.scalars().all()
        elevation_cells = {
            c.snail_path: {"elevation_m": c.elevation_m, "slope_deg": c.slope_deg, "aspect_deg": c.aspect_deg}
            for c in elev_cells
        }
        set_cached_terrain_data(sid, terrain_cells, elevation_cells)
        # Also cache centroids (extend the terrain cache entry)
        _cache_entry = get_cached_terrain_data(sid)
        if _cache_entry is not None:
            _cache_entry["cell_centroids"] = cell_centroids

    if not terrain_cells:
        return {"path": [], "cost": 0, "cells": 0}

    # Build centroids from grid if not available in cache
    if not cell_centroids:
        for path in terrain_cells:
            try:
                center = grid_service.snail_to_center(path)
                if center:
                    cell_centroids[path] = (center.y, center.x)
            except Exception:
                pass

    if not cell_centroids:
        return {"path": [], "cost": 0, "cells": 0}

    # Get user's side for minefield discovery filtering
    from backend.models.session import SessionParticipant
    part_result = await db.execute(
        select(SessionParticipant).where(
            SessionParticipant.session_id == session_id,
            SessionParticipant.user_id == current_user.id,
        )
    )
    participant = part_result.scalar_one_or_none()
    side = participant.side if participant else "blue"
    side_str = side.value if hasattr(side, "value") else str(side)

    # Load active map objects (for obstacle avoidance)
    from backend.models.map_object import MapObject
    obj_result = await db.execute(
        select(MapObject).where(
            MapObject.session_id == session_id,
            MapObject.is_active == True,  # noqa: E712
        )
    )
    map_objects = obj_result.scalars().all()

    # ── Load tactical data (enemy contacts + friendly positions) ──
    enemy_positions: list[tuple[float, float, float]] = []
    friendly_positions: list[tuple[float, float]] = []

    if unit_id:
        from backend.models.contact import Contact
        from backend.models.unit import Unit as UnitModel
        from geoalchemy2.shape import to_shape as _to_shape

        # Load known enemy contacts for this side
        try:
            contact_result = await db.execute(
                select(Contact).where(
                    Contact.session_id == session_id,
                    Contact.observing_side == side_str,
                    Contact.is_stale == False,  # noqa: E712
                )
            )
            contacts = contact_result.scalars().all()
            for c in contacts:
                if c.location_estimate is None:
                    continue
                try:
                    cp = _to_shape(c.location_estimate)
                    det_range = c.location_accuracy_m or 1500.0
                    enemy_positions.append((cp.y, cp.x, det_range))
                except Exception:
                    pass
        except Exception:
            pass

        # Load friendly unit positions
        try:
            friendly_result = await db.execute(
                select(UnitModel).where(
                    UnitModel.session_id == session_id,
                    UnitModel.side == side_str,
                    UnitModel.is_destroyed == False,  # noqa: E712
                )
            )
            friendlies = friendly_result.scalars().all()
            for fu in friendlies:
                if fu.position is None or str(fu.id) == unit_id:
                    continue
                try:
                    fp = _to_shape(fu.position)
                    friendly_positions.append((fp.y, fp.x))
                except Exception:
                    pass
        except Exception:
            pass

    # Build pathfinder with session-cached or DB-persisted static graph
    from backend.services.pathfinding_service import PathfindingService, load_or_build_static_graph
    sid_str = str(session_id)
    gd_settings = dict(grid_def.settings_json) if grid_def and grid_def.settings_json else None
    static_graph, cell_centroids = load_or_build_static_graph(
        sid_str,
        terrain_cells,
        elevation_cells,
        cell_centroids if cell_centroids else None,
        grid_service,
        grid_def_settings_json=gd_settings,
    )

    pathfinder = PathfindingService(
        terrain_cells=terrain_cells,
        grid_service=grid_service,
        map_objects=map_objects if map_objects else None,
        side=side_str,
        enemy_positions=enemy_positions if enemy_positions else None,
        friendly_positions=friendly_positions if friendly_positions else None,
        speed_mode=speed_mode,
        static_graph=static_graph,
    )

    # Run CPU-bound A* in thread pool to avoid blocking the async event loop
    import asyncio as _aio
    import functools as _ft
    loop = _aio.get_running_loop()
    path = await loop.run_in_executor(
        None,
        _ft.partial(pathfinder.find_path, from_lat, from_lon, to_lat, to_lon),
    )
    if path is None:
        return {"path": [], "cost": 0, "cells": 0}

    # Convert to [[lat, lon], ...] for JSON
    path_list = [[round(lat, 7), round(lon, 7)] for lat, lon in path]

    # Cache result
    if cache_key_snail and to_snail:
        _set_cached_path(sid, cache_key_snail, to_snail, path_list)

    return {"path": path_list, "cost": 0, "cells": len(path_list)}


# ── LOS (Line-of-Sight) Check ──────────────────────────────────

@router.get("/{session_id}/los-check")
async def check_line_of_sight(
    session_id: uuid.UUID,
    from_lat: float = Query(...),
    from_lon: float = Query(...),
    to_lat: float = Query(...),
    to_lon: float = Query(...),
    eye_height: float = Query(2.0),
    db: DB = None,
):
    """Check if there is line-of-sight between two points.

    Returns {has_los: bool, distance_m: float, from_elevation_m, to_elevation_m,
             blocking_terrain: str|null, blocking_elevation_m: float|null}.
    """
    from backend.engine.terrain import TerrainService, get_cached_terrain_data, set_cached_terrain_data
    from backend.services.los_service import LOSService, _distance_m

    sid = str(session_id)

    # Build TerrainService from cache or DB
    cached = get_cached_terrain_data(sid)
    terrain_cells = None
    elevation_cells = None

    if cached:
        terrain_cells = cached.get("terrain_cells")
        elevation_cells = cached.get("elevation_cells")
    else:
        tc_result = await db.execute(
            select(TerrainCell.snail_path, TerrainCell.terrain_type)
            .where(TerrainCell.session_id == session_id)
        )
        tc_rows = tc_result.all()
        terrain_cells = {r[0]: r[1] for r in tc_rows} if tc_rows else None

        ec_result = await db.execute(
            select(
                ElevationCell.snail_path,
                ElevationCell.elevation_m,
                ElevationCell.slope_deg,
                ElevationCell.aspect_deg,
            ).where(ElevationCell.session_id == session_id)
        )
        ec_rows = ec_result.all()
        elevation_cells = {
            r[0]: {"elevation_m": r[1], "slope_deg": r[2], "aspect_deg": r[3]}
            for r in ec_rows
        } if ec_rows else None

        if terrain_cells:
            set_cached_terrain_data(sid, terrain_cells, elevation_cells)

    # Build grid service
    gd_result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    gd = gd_result.scalar_one_or_none()
    grid_service = GridService(gd) if gd else None

    terrain_svc = TerrainService(
        terrain_cells=terrain_cells,
        elevation_cells=elevation_cells,
        grid_service=grid_service,
    )

    los = LOSService(terrain_svc)
    has_los = los.has_los(from_lon, from_lat, to_lon, to_lat, eye_height=eye_height)
    dist = _distance_m(from_lat, from_lon, to_lat, to_lon)

    from_elev = terrain_svc.get_elevation_at(from_lon, from_lat)
    to_elev = terrain_svc.get_elevation_at(to_lon, to_lat)

    # If LOS blocked, find what blocks it
    blocking_terrain = None
    blocking_elev = None
    if not has_los:
        # Walk the ray to find the blocking point
        from backend.services.los_service import TERRAIN_OBSTACLE_HEIGHT, MIN_VISIBILITY_BUDGET
        step_m = 100.0
        num_steps = max(2, int(dist / step_m))
        from_eye = from_elev + eye_height
        to_eye = to_elev + eye_height
        vis_budget = 1.0
        for s in range(1, num_steps):
            t = s / num_steps
            s_lat = from_lat + t * (to_lat - from_lat)
            s_lon = from_lon + t * (to_lon - from_lon)
            los_elev = from_eye + t * (to_eye - from_eye)
            g_elev = terrain_svc.get_elevation_at(s_lon, s_lat)
            tt = terrain_svc.get_terrain_at(s_lon, s_lat)
            obs_h = TERRAIN_OBSTACLE_HEIGHT.get(tt, 0.0)
            eff = g_elev + obs_h
            if eff > los_elev:
                blocking_terrain = tt
                blocking_elev = round(g_elev, 1)
                break
            if t < 0.9:
                vis_f = terrain_svc.visibility_factor(s_lon, s_lat)
                if vis_f < 1.0:
                    absorption = (1.0 - vis_f) * (step_m / 100.0)
                    vis_budget -= absorption
                    if vis_budget < MIN_VISIBILITY_BUDGET:
                        blocking_terrain = tt
                        blocking_elev = round(g_elev, 1)
                        break

    return {
        "has_los": has_los,
        "distance_m": round(dist, 1),
        "from_elevation_m": round(from_elev, 1),
        "to_elevation_m": round(to_elev, 1),
        "blocking_terrain": blocking_terrain,
        "blocking_elevation_m": blocking_elev,
    }
