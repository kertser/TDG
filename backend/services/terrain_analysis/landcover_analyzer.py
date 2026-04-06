"""
ESA WorldCover Land Cover Analyzer.

Fetches ESA WorldCover 2021 data for cell centroids using rasterio (COG HTTP
range requests) if available, or falls back to a simplified approach.

ESA class ID → terrain type mapping:
  10 → forest   (tree cover)
  20 → scrub    (shrubland)
  30 → open     (grassland)
  40 → fields   (cropland)
  50 → urban    (built-up)
  60 → desert   (bare/sparse vegetation)
  70 → desert   (snow and ice → treat as bare)
  80 → water    (permanent water bodies)
  90 → marsh    (herbaceous wetland)
  95 → forest   (mangroves)
  100 → marsh   (moss and lichen)
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# ESA WorldCover class ID → terrain type
ESA_CLASS_MAP = {
    10: "forest",    # Tree cover
    20: "scrub",     # Shrubland
    30: "open",      # Grassland
    40: "fields",    # Cropland
    50: "urban",     # Built-up
    60: "desert",    # Bare / sparse vegetation
    70: "desert",    # Snow and ice
    80: "water",     # Permanent water bodies
    90: "marsh",     # Herbaceous wetland
    95: "forest",    # Mangroves
    100: "marsh",    # Moss and lichen
}


async def analyze_landcover_rasterio(
    centroids: list[tuple[str, float, float]],
) -> dict[str, tuple[str, int]]:
    """
    Analyze land cover using rasterio to read ESA WorldCover COG tiles.

    Args:
        centroids: list of (snail_path, lat, lon) tuples

    Returns:
        dict[snail_path → (terrain_type, esa_class_id)]
    """
    try:
        import rasterio
        from rasterio.crs import CRS
    except ImportError:
        logger.warning("rasterio not available — skipping ESA WorldCover analysis")
        return {}

    from backend.services.terrain_analysis.esa_tiles import tile_url

    results: dict[str, tuple[str, int]] = {}
    # Group centroids by tile URL for efficient reading
    tile_groups: dict[str, list[tuple[str, float, float]]] = {}
    for snail_path, lat, lon in centroids:
        url = tile_url(lat, lon)
        tile_groups.setdefault(url, []).append((snail_path, lat, lon))

    for url, points in tile_groups.items():
        try:
            env = rasterio.Env(
                GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
                CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif",
                GDAL_HTTP_TIMEOUT=30,
            )
            with env:
                with rasterio.open(url) as src:
                    for snail_path, lat, lon in points:
                        try:
                            row, col = src.index(lon, lat)
                            window = rasterio.windows.Window(col, row, 1, 1)
                            data = src.read(1, window=window)
                            class_id = int(data[0, 0])
                            terrain = ESA_CLASS_MAP.get(class_id, "open")
                            results[snail_path] = (terrain, class_id)
                        except Exception:
                            pass
            logger.info(f"ESA tile {url}: classified {len([p for p in points if p[0] in results])}/{len(points)} points")
        except Exception as e:
            logger.warning(f"Failed to read ESA tile {url}: {e}")

    return results


async def analyze_landcover_fallback(
    centroids: list[tuple[str, float, float]],
) -> dict[str, tuple[str, int]]:
    """
    Fallback land cover analysis when rasterio is not available.
    Returns empty dict — OSM data will be the sole source.
    """
    logger.info("Land cover fallback: no rasterio, returning empty (OSM-only mode)")
    return {}


async def analyze_landcover(
    centroids: list[tuple[str, float, float]],
) -> dict[str, tuple[str, int]]:
    """
    Analyze land cover for a list of centroids.
    Tries rasterio first, falls back gracefully.

    Args:
        centroids: list of (snail_path, lat, lon) tuples

    Returns:
        dict[snail_path → (terrain_type, esa_class_id)]
    """
    if not centroids:
        return {}

    # Try rasterio approach
    try:
        import rasterio  # noqa: F401
        return await analyze_landcover_rasterio(centroids)
    except ImportError:
        pass

    # Fallback
    return await analyze_landcover_fallback(centroids)

