"""
Terrain Merger — merges results from OSM, ESA, and Elevation analyzers.

Priority order (highest to lowest):
  1. Admin manual overrides (source='manual') — never overwritten
  2. OSM infrastructure features (road, bridge, urban, water — high confidence vectors)
  3. Elevation override: slope > 20° → mountain (regardless of other sources)
  4. ESA WorldCover land cover (forest, scrub, fields, marsh, desert, open)
  5. Default: "open"
"""

from __future__ import annotations

import logging

from shapely.geometry import Point
from shapely.strtree import STRtree

from backend.engine.terrain import TERRAIN_MODIFIERS
from backend.services.terrain_analysis.osm_analyzer import OSMFeature

logger = logging.getLogger(__name__)

# OSM terrain types that override ESA land cover (infrastructure)
OSM_PRIORITY_TYPES = {"road", "bridge", "urban", "water"}

# Slope threshold for mountain classification
MOUNTAIN_SLOPE_THRESHOLD = 20.0  # degrees


def merge_terrain_fast(
    snail_path: str,
    centroid_lat: float,
    centroid_lon: float,
    osm_features: list[OSMFeature],
    osm_tree: STRtree | None,
    osm_geoms: list,
    osm_distance: float,
    esa_result: tuple[str, int] | None,
    elevation_data: dict | None,
    existing_source: str | None = None,
) -> dict:
    """
    Optimized merge using pre-built STRtree for O(log n) spatial lookups.
    Same logic as merge_terrain but uses spatial index instead of brute force.
    """
    if existing_source == "manual":
        return {"skip": True}

    terrain_type = "open"
    source = "default"
    confidence = 0.3
    raw_tags = None

    # Check OSM features using spatial index
    pt = Point(centroid_lon, centroid_lat)
    osm_match = None

    if osm_tree is not None and osm_geoms:
        # Query with buffer for nearby features
        query_geom = pt.buffer(osm_distance)
        candidate_indices = osm_tree.query(query_geom)
        for idx in candidate_indices:
            feature = osm_features[idx]
            try:
                if feature.geometry.contains(pt) or feature.geometry.distance(pt) < osm_distance:
                    if osm_match is None or feature.priority < osm_match.priority:
                        osm_match = feature
            except Exception:
                continue

    if osm_match and osm_match.terrain_type in OSM_PRIORITY_TYPES:
        terrain_type = osm_match.terrain_type
        source = "osm"
        confidence = osm_match.confidence
        raw_tags = osm_match.tags
    elif esa_result:
        terrain_type = esa_result[0]
        source = "landcover"
        confidence = 0.75
        raw_tags = {"esa_class_id": esa_result[1]}
    elif osm_match:
        terrain_type = osm_match.terrain_type
        source = "osm"
        confidence = osm_match.confidence
        raw_tags = osm_match.tags

    # Elevation override: steep slopes → mountain
    slope_deg = None
    elevation_m = None
    if elevation_data:
        elevation_m = elevation_data.get("elevation_m")
        slope_deg = elevation_data.get("slope_deg")
        if slope_deg and slope_deg > MOUNTAIN_SLOPE_THRESHOLD:
            terrain_type = "mountain"
            source = "elevation"
            confidence = 0.85

    modifiers = TERRAIN_MODIFIERS.get(terrain_type, TERRAIN_MODIFIERS["open"]).copy()

    return {
        "terrain_type": terrain_type,
        "source": source,
        "confidence": confidence,
        "raw_tags": raw_tags,
        "elevation_m": elevation_m,
        "slope_deg": slope_deg,
        "modifiers": modifiers,
    }


def merge_terrain(
    snail_path: str,
    centroid_lat: float,
    centroid_lon: float,
    osm_features: list[OSMFeature],
    esa_result: tuple[str, int] | None,
    elevation_data: dict | None,
    existing_source: str | None = None,
) -> dict:
    """
    Merge terrain data from multiple sources for a single cell.

    Returns dict with:
      terrain_type, source, confidence, raw_tags, elevation_m, slope_deg, modifiers
    """
    # 1. Never overwrite manual cells
    if existing_source == "manual":
        return {"skip": True}

    terrain_type = "open"
    source = "default"
    confidence = 0.3
    raw_tags = None

    # 2. Check OSM features (sorted by priority — infrastructure wins)
    pt = Point(centroid_lon, centroid_lat)
    osm_match = None
    for feature in osm_features:
        try:
            if feature.geometry.contains(pt) or feature.geometry.distance(pt) < 0.0002:
                if osm_match is None or feature.priority < osm_match.priority:
                    osm_match = feature
        except Exception:
            continue

    if osm_match and osm_match.terrain_type in OSM_PRIORITY_TYPES:
        terrain_type = osm_match.terrain_type
        source = "osm"
        confidence = osm_match.confidence
        raw_tags = osm_match.tags
    elif esa_result:
        # 4. ESA land cover
        terrain_type = esa_result[0]
        source = "landcover"
        confidence = 0.75
        raw_tags = {"esa_class_id": esa_result[1]}
    elif osm_match:
        # Non-infrastructure OSM match (forest, marsh, fields from OSM)
        terrain_type = osm_match.terrain_type
        source = "osm"
        confidence = osm_match.confidence
        raw_tags = osm_match.tags

    # 3. Elevation override: steep slopes → mountain
    slope_deg = None
    elevation_m = None
    if elevation_data:
        elevation_m = elevation_data.get("elevation_m")
        slope_deg = elevation_data.get("slope_deg")
        if slope_deg and slope_deg > MOUNTAIN_SLOPE_THRESHOLD:
            terrain_type = "mountain"
            source = "elevation"
            confidence = 0.85

    # Get modifiers for the terrain type
    modifiers = TERRAIN_MODIFIERS.get(terrain_type, TERRAIN_MODIFIERS["open"]).copy()

    return {
        "terrain_type": terrain_type,
        "source": source,
        "confidence": confidence,
        "raw_tags": raw_tags,
        "elevation_m": elevation_m,
        "slope_deg": slope_deg,
        "modifiers": modifiers,
    }

