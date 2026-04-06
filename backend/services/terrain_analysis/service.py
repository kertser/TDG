"""
Terrain Analysis Service — main orchestrator.

Coordinates OSM, ESA WorldCover, and Elevation analyzers,
merges results, and writes TerrainCell + ElevationCell rows to the DB.

Supports both one-shot and streaming (SSE progress) modes.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import AsyncGenerator

from shapely.strtree import STRtree

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.grid import GridDefinition
from backend.models.terrain_cell import TerrainCell
from backend.models.elevation_cell import ElevationCell
from backend.services.grid_service import GridService
from backend.services.terrain_analysis.osm_analyzer import analyze_osm
from backend.services.terrain_analysis.landcover_analyzer import analyze_landcover
from backend.services.terrain_analysis.elevation_analyzer import (
    fetch_elevations_concurrent,
    compute_slope_aspect,
)
from backend.services.terrain_analysis.merger import merge_terrain_fast

logger = logging.getLogger(__name__)


# ── Streaming (SSE) version ──────────────────────────────────

async def analyze_grid_streaming(
    session_id: uuid.UUID,
    db: AsyncSession,
    depth: int = 1,
    force: bool = False,
    skip_elevation: bool = False,
) -> AsyncGenerator[dict, None]:
    """
    Async generator that yields progress dicts during terrain analysis.
    Each yield is an SSE event with {step, message, progress, ...}.
    """
    t0 = time.time()

    # ── 1. Load grid definition ──────────────────────────
    yield {"step": "init", "message": "Loading grid definition...", "progress": 0.0}

    result = await db.execute(
        select(GridDefinition).where(GridDefinition.session_id == session_id)
    )
    grid_def = result.scalar_one_or_none()
    if grid_def is None:
        yield {"step": "error", "message": "No grid definition for this session", "progress": -1}
        return

    grid_service = GridService(grid_def)

    # ── 2. Enumerate cells at target depth ───────────────
    yield {"step": "enumerate", "message": f"Enumerating cells at depth {depth}...", "progress": 0.02}

    centroids = grid_service.enumerate_cells(depth=depth)
    if not centroids:
        yield {"step": "error", "message": "Grid has no cells at the requested depth", "progress": -1}
        return

    cell_size_m = grid_def.base_square_size_m / (3 ** depth) if depth > 0 else grid_def.base_square_size_m
    yield {
        "step": "enumerate_done", "progress": 0.05,
        "message": f"Found {len(centroids)} cells ({cell_size_m:.0f}m resolution)",
    }

    # Load existing manual cells (to preserve them)
    existing_manual = set()
    existing_sources: dict[str, str] = {}
    if not force:
        result = await db.execute(
            select(TerrainCell.snail_path, TerrainCell.source).where(
                TerrainCell.session_id == session_id
            )
        )
        for row in result:
            existing_sources[row[0]] = row[1]
            if row[1] == "manual":
                existing_manual.add(row[0])

    # ── 3. Compute grid bounding box for OSM ─────────────
    all_lats = [c[1] for c in centroids]
    all_lons = [c[2] for c in centroids]
    bbox_south = min(all_lats) - 0.005
    bbox_north = max(all_lats) + 0.005
    bbox_west = min(all_lons) - 0.005
    bbox_east = max(all_lons) + 0.005

    # ── 4. Run OSM analyzer ──────────────────────────────
    yield {"step": "osm", "message": "Fetching OSM data (roads, buildings, water, forest)...", "progress": 0.08}

    osm_features = await analyze_osm(bbox_south, bbox_west, bbox_north, bbox_east)
    osm_count = len(osm_features)
    yield {
        "step": "osm_done", "progress": 0.25,
        "message": f"OSM: {osm_count} features found" if osm_count else "OSM: no features (Overpass may have timed out — using fallback sources)",
    }

    # Build spatial index for OSM features (STRtree)
    osm_tree = None
    osm_geoms = []
    if osm_features:
        osm_geoms = [f.geometry for f in osm_features]
        osm_tree = STRtree(osm_geoms)

    # ── 5. Run ESA land cover analyzer ───────────────────
    yield {"step": "landcover", "message": "Analyzing land cover (ESA WorldCover)...", "progress": 0.28}

    esa_results = await analyze_landcover(centroids)
    yield {"step": "landcover_done", "message": f"Land cover: {len(esa_results)} cells classified", "progress": 0.35}

    # ── 6. Run elevation analyzer ────────────────────────
    elevation_data: dict[str, dict] = {}
    if not skip_elevation:
        from backend.services.terrain_analysis.elevation_analyzer import _HAS_RASTERIO
        elev_source = "Copernicus DEM (rasterio)" if _HAS_RASTERIO else "Open-Meteo API"
        yield {
            "step": "elevation", "progress": 0.38,
            "message": f"Fetching elevation via {elev_source} ({len(centroids)} points)...",
        }

        elevation_raw = await fetch_elevations_concurrent(centroids)

        if elevation_raw:
            yield {
                "step": "elevation_slope", "progress": 0.60,
                "message": f"Computing slope/aspect for {len(elevation_raw)} cells...",
            }
            neighbors = _build_neighbor_map_fast(centroids)
            elevation_data = compute_slope_aspect(elevation_raw, neighbors, cell_size_m)

        elev_count = len(elevation_data)
        yield {
            "step": "elevation_done", "progress": 0.65,
            "message": f"Elevation: {elev_count} cells with slope data",
        }
    else:
        yield {"step": "elevation_skipped", "message": "Elevation skipped", "progress": 0.65}

    # ── 7. Clear existing non-manual cells if force ──────
    if force:
        yield {"step": "clear", "message": "Clearing existing terrain data...", "progress": 0.67}
        await db.execute(
            delete(TerrainCell).where(
                TerrainCell.session_id == session_id,
                TerrainCell.source != "manual",
            )
        )
        await db.execute(
            delete(ElevationCell).where(
                ElevationCell.session_id == session_id,
            )
        )
        await db.flush()

    # ── 8. Merge and write to DB ─────────────────────────
    yield {"step": "merge", "message": f"Merging terrain data for {len(centroids)} cells...", "progress": 0.70}

    # Distance threshold for OSM matching: half the cell diagonal
    osm_distance = max(0.00005, cell_size_m * 0.7 / 111000)  # approx degrees

    cells_created = 0
    cells_updated = 0
    cells_skipped = 0
    elev_created = 0
    total = len(centroids)
    batch_size = 500
    pending_terrain_cells = []
    pending_elev_cells = []

    for idx, (snail_path, lat, lon) in enumerate(centroids):
        # Progress every 500 cells
        if idx % batch_size == 0 and idx > 0:
            pct = 0.70 + 0.25 * (idx / total)
            yield {
                "step": "merge_progress",
                "message": f"Processing cells: {idx}/{total} ({cells_created} new, {cells_updated} updated)",
                "progress": round(pct, 3),
            }
            # Flush pending objects periodically
            if pending_terrain_cells:
                db.add_all(pending_terrain_cells)
                pending_terrain_cells = []
            if pending_elev_cells:
                db.add_all(pending_elev_cells)
                pending_elev_cells = []
            await db.flush()

        # Skip manual cells
        if snail_path in existing_manual:
            cells_skipped += 1
            continue

        # Merge data from all sources using spatial index
        merged = merge_terrain_fast(
            snail_path=snail_path,
            centroid_lat=lat,
            centroid_lon=lon,
            osm_features=osm_features,
            osm_tree=osm_tree,
            osm_geoms=osm_geoms,
            osm_distance=osm_distance,
            esa_result=esa_results.get(snail_path),
            elevation_data=elevation_data.get(snail_path),
            existing_source=existing_sources.get(snail_path),
        )

        if merged.get("skip"):
            cells_skipped += 1
            continue

        # Upsert TerrainCell
        if snail_path in existing_sources and not force:
            result = await db.execute(
                select(TerrainCell).where(
                    TerrainCell.session_id == session_id,
                    TerrainCell.snail_path == snail_path,
                )
            )
            cell = result.scalar_one_or_none()
            if cell:
                cell.terrain_type = merged["terrain_type"]
                cell.source = merged["source"]
                cell.confidence = merged["confidence"]
                cell.modifiers = merged["modifiers"]
                cell.raw_tags = merged.get("raw_tags")
                cell.elevation_m = merged.get("elevation_m")
                cell.slope_deg = merged.get("slope_deg")
                cells_updated += 1
            else:
                pending_terrain_cells.append(_make_terrain_cell(
                    session_id, snail_path, lat, lon, depth, merged
                ))
                cells_created += 1
        else:
            pending_terrain_cells.append(_make_terrain_cell(
                session_id, snail_path, lat, lon, depth, merged
            ))
            cells_created += 1

        # Create ElevationCell
        elev = elevation_data.get(snail_path)
        if elev:
            pending_elev_cells.append(_make_elevation_cell(
                session_id, snail_path, lat, lon, depth, elev
            ))
            elev_created += 1

    # Final flush
    if pending_terrain_cells:
        db.add_all(pending_terrain_cells)
    if pending_elev_cells:
        db.add_all(pending_elev_cells)

    yield {"step": "write", "message": "Committing to database...", "progress": 0.96}
    await db.flush()

    duration = time.time() - t0
    summary = {
        "status": "complete",
        "cells_total": total,
        "cells_created": cells_created,
        "cells_updated": cells_updated,
        "cells_skipped": cells_skipped,
        "elevation_cells": elev_created,
        "osm_features": len(osm_features),
        "esa_classified": len(esa_results),
        "duration_s": round(duration, 2),
        "depth": depth,
        "cell_size_m": round(cell_size_m, 1),
    }
    logger.info(f"Terrain analysis complete: {summary}")

    yield {"step": "complete", "message": "Analysis complete", "progress": 1.0, "summary": summary}


# ── Legacy non-streaming wrapper ─────────────────────────────

async def analyze_grid(
    session_id: uuid.UUID,
    db: AsyncSession,
    depth: int = 1,
    force: bool = False,
    skip_elevation: bool = False,
) -> dict:
    """
    Non-streaming wrapper — collects all events and returns final summary.
    """
    summary = {}
    async for event in analyze_grid_streaming(
        session_id=session_id, db=db, depth=depth,
        force=force, skip_elevation=skip_elevation,
    ):
        if event.get("step") == "complete":
            summary = event.get("summary", {})
        elif event.get("step") == "error":
            raise ValueError(event.get("message", "Analysis failed"))
    return summary


# ── Helper functions ─────────────────────────────────────────

def _make_terrain_cell(
    session_id: uuid.UUID,
    snail_path: str,
    lat: float,
    lon: float,
    depth: int,
    merged: dict,
) -> TerrainCell:
    """Create a new TerrainCell ORM object (not yet added to session)."""
    return TerrainCell(
        session_id=session_id,
        snail_path=snail_path,
        depth=depth,
        terrain_type=merged["terrain_type"],
        modifiers=merged["modifiers"],
        source=merged["source"],
        confidence=merged["confidence"],
        centroid_lat=lat,
        centroid_lon=lon,
        elevation_m=merged.get("elevation_m"),
        slope_deg=merged.get("slope_deg"),
        raw_tags=merged.get("raw_tags"),
    )


def _make_elevation_cell(
    session_id: uuid.UUID,
    snail_path: str,
    lat: float,
    lon: float,
    depth: int,
    elev_data: dict,
) -> ElevationCell:
    """Create a new ElevationCell ORM object."""
    return ElevationCell(
        session_id=session_id,
        snail_path=snail_path,
        depth=depth,
        elevation_m=elev_data.get("elevation_m", 0.0),
        slope_deg=elev_data.get("slope_deg", 0.0),
        aspect_deg=elev_data.get("aspect_deg"),
        centroid_lat=lat,
        centroid_lon=lon,
    )


def _build_neighbor_map_fast(
    centroids: list[tuple[str, float, float]],
) -> dict[str, dict[str, str | None]]:
    """
    O(n) neighbor map using spatial hash grid.
    Maps each snail_path to its N/S/E/W neighbor snail paths.
    """
    if len(centroids) < 2:
        return {}

    # Collect sorted unique lat/lon values to find grid spacing
    lats = sorted(set(c[1] for c in centroids))
    lons = sorted(set(c[2] for c in centroids))

    # Compute diffs and use a percentile to get the real grid spacing
    # (avoids picking up tiny floating point noise as the "spacing")
    lat_diffs = sorted(lats[i + 1] - lats[i] for i in range(len(lats) - 1) if lats[i + 1] - lats[i] > 1e-5)
    lon_diffs = sorted(lons[i + 1] - lons[i] for i in range(len(lons) - 1) if lons[i + 1] - lons[i] > 1e-5)

    # Use median of significant diffs for robust spacing
    dlat = lat_diffs[len(lat_diffs) // 2] if lat_diffs else 0.003
    dlon = lon_diffs[len(lon_diffs) // 2] if lon_diffs else 0.003

    if dlat < 1e-6:
        dlat = 0.003
    if dlon < 1e-6:
        dlon = 0.003

    # Build spatial hash: (row, col) → snail_path
    # Use floor division with half-cell offset for stable binning
    half_dlat = dlat / 2
    half_dlon = dlon / 2
    pos_to_path: dict[tuple[int, int], str] = {}
    path_to_key: dict[str, tuple[int, int]] = {}

    for snail_path, lat, lon in centroids:
        key = (round((lat + half_dlat) / dlat), round((lon + half_dlon) / dlon))
        pos_to_path[key] = snail_path
        path_to_key[snail_path] = key

    # Look up neighbors by offset
    neighbors: dict[str, dict[str, str | None]] = {}
    for snail_path, (r, c) in path_to_key.items():
        neighbors[snail_path] = {
            "n": pos_to_path.get((r + 1, c)),
            "s": pos_to_path.get((r - 1, c)),
            "e": pos_to_path.get((r, c + 1)),
            "w": pos_to_path.get((r, c - 1)),
        }

    return neighbors



