"""
OSM Overpass Analyzer — fetches OSM features and classifies terrain from tags.

Given a grid bounding box, queries the Overpass API for landuse, natural,
highway, waterway, building tags. Returns a list of classified features
with Shapely geometries and terrain type assignments.

Priority-ranked tag → terrain type mapping (highest priority first):
  P1: bridge=yes           → bridge
  P2: highway=*            → road
  P3: building=*           → urban
  P4: landuse=residential  → urban
  P5: waterway=river/stream → water
  P6: natural=water        → water
  P7: natural=wood/forest  → forest
  P8: natural=wetland      → marsh
  P9: landuse=farmland/meadow → fields
  P10: landuse=orchard/vineyard → orchard
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from backend.services.terrain_analysis.overpass_queries import (
    query_overpass_tiled,
)

logger = logging.getLogger(__name__)


@dataclass
class OSMFeature:
    """A classified terrain feature from OSM data."""
    terrain_type: str
    geometry: object  # Shapely geometry
    priority: int
    tags: dict = field(default_factory=dict)
    confidence: float = 0.9


# Tag → (terrain_type, priority) mapping
# Lower priority number = higher precedence
TAG_RULES: list[tuple[str, str, str, int]] = [
    # (tag_key, tag_value_pattern, terrain_type, priority)
    # tag_value_pattern: "*" matches any value, otherwise exact or startswith match
    ("bridge", "yes", "bridge", 1),
    ("highway", "motorway", "road", 2),
    ("highway", "trunk", "road", 2),
    ("highway", "primary", "road", 2),
    ("highway", "secondary", "road", 2),
    ("highway", "tertiary", "road", 2),
    ("highway", "unclassified", "road", 3),
    ("highway", "residential", "road", 3),
    ("highway", "service", "road", 4),
    ("highway", "track", "road", 5),
    ("building", "*", "urban", 6),
    ("landuse", "residential", "urban", 7),
    ("landuse", "commercial", "urban", 7),
    ("landuse", "industrial", "urban", 7),
    ("landuse", "retail", "urban", 7),
    ("waterway", "river", "water", 8),
    ("waterway", "stream", "water", 8),
    ("waterway", "canal", "water", 8),
    ("natural", "water", "water", 8),
    ("water", "*", "water", 8),
    ("natural", "wood", "forest", 9),
    ("landuse", "forest", "forest", 9),
    ("natural", "wetland", "marsh", 10),
    ("wetland", "*", "marsh", 10),
    ("landuse", "farmland", "fields", 11),
    ("landuse", "meadow", "fields", 11),
    ("landuse", "grass", "open", 12),
    ("natural", "grassland", "open", 12),
    ("natural", "scrub", "scrub", 13),
    ("natural", "heath", "scrub", 13),
    ("landuse", "orchard", "orchard", 14),
    ("landuse", "vineyard", "orchard", 14),
    ("natural", "bare_rock", "mountain", 15),
    ("natural", "scree", "mountain", 15),
    ("natural", "sand", "desert", 16),
    ("natural", "beach", "desert", 16),
]


def _classify_element(tags: dict) -> tuple[str, int] | None:
    """
    Classify an OSM element by its tags using priority rules.
    Returns (terrain_type, priority) or None if no match.
    """
    best: tuple[str, int] | None = None
    for tag_key, tag_val, terrain_type, priority in TAG_RULES:
        if tag_key not in tags:
            continue
        actual_val = tags[tag_key]
        if tag_val == "*" or actual_val == tag_val or actual_val.startswith(tag_val):
            if best is None or priority < best[1]:
                best = (terrain_type, priority)
    return best


def _element_to_geometry(element: dict) -> object | None:
    """
    Convert an Overpass element to a Shapely geometry.
    Handles ways (polygons and lines) and relations (multipolygons).
    """
    etype = element.get("type")

    if etype == "way":
        geom = element.get("geometry", [])
        if not geom or len(geom) < 2:
            return None
        coords = [(pt["lon"], pt["lat"]) for pt in geom]

        # If closed → polygon, else line with buffer
        if len(coords) >= 4 and coords[0] == coords[-1]:
            try:
                poly = Polygon(coords)
                if poly.is_valid and not poly.is_empty:
                    return poly
            except Exception:
                pass

        # Line feature (roads, waterways) → buffer to polygon
        try:
            line = LineString(coords)
            if line.is_valid and not line.is_empty:
                # Buffer roads/rivers by a small amount (~15m in degrees ≈ 0.00015)
                return line.buffer(0.00015)
        except Exception:
            pass

    elif etype == "relation":
        # Try to build multipolygon from relation members
        members = element.get("members", [])
        outer_rings = []
        for member in members:
            if member.get("role") in ("outer", "") and member.get("geometry"):
                coords = [(pt["lon"], pt["lat"]) for pt in member["geometry"]]
                if len(coords) >= 4:
                    try:
                        ring = Polygon(coords)
                        if ring.is_valid and not ring.is_empty:
                            outer_rings.append(ring)
                    except Exception:
                        pass
        if outer_rings:
            try:
                return unary_union(outer_rings)
            except Exception:
                pass

    return None


async def analyze_osm(
    south: float, west: float, north: float, east: float,
) -> list[OSMFeature]:
    """
    Query OSM Overpass API for the bounding box and return classified features.

    Returns list of OSMFeature with Shapely geometries and terrain types,
    sorted by priority (highest priority = lowest number first).
    """
    logger.info(f"OSM analysis: bbox=({south},{west},{north},{east})")

    data = await query_overpass_tiled(south, west, north, east)

    elements = data.get("elements", [])
    logger.info(f"OSM returned {len(elements)} elements")

    features: list[OSMFeature] = []
    classified = 0
    skipped_geom = 0

    for elem in elements:
        tags = elem.get("tags", {})
        classification = _classify_element(tags)
        if classification is None:
            continue

        terrain_type, priority = classification
        geom = _element_to_geometry(elem)
        if geom is None:
            skipped_geom += 1
            continue

        features.append(OSMFeature(
            terrain_type=terrain_type,
            geometry=geom,
            priority=priority,
            tags=tags,
            confidence=0.9 if priority <= 5 else 0.8,
        ))
        classified += 1

    # Sort by priority (most important first)
    features.sort(key=lambda f: f.priority)

    logger.info(
        f"OSM classified {classified} features, "
        f"skipped {skipped_geom} (no geometry), "
        f"unmatched {len(elements) - classified - skipped_geom}"
    )
    return features


