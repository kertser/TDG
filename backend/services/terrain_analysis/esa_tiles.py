"""
ESA WorldCover tile index — maps lat/lon to S3 tile URLs.

ESA WorldCover 2021 v200 tiles are 3°×3° Cloud-Optimized GeoTIFF files
hosted on AWS S3 at:
  s3://esa-worldcover/v200/2021/map/ESA_WorldCover_10m_2021_v200_{tile_id}_Map.tif

Tile IDs follow the pattern: N48E003 (north lat, east lon of SW corner).
"""

from __future__ import annotations


ESA_BASE_URL = "https://esa-worldcover.s3.eu-central-1.amazonaws.com"
ESA_PATH = "v200/2021/map"


def _tile_id(lat: float, lon: float) -> str:
    """
    Compute ESA WorldCover tile ID for a given coordinate.
    Tiles are 3°×3° aligned to multiples of 3.
    The tile ID refers to the SW corner of the tile.
    """
    # Floor to nearest multiple of 3
    tile_lat = int(lat // 3) * 3
    tile_lon = int(lon // 3) * 3

    # Handle negative coordinates
    if lat < 0:
        tile_lat = -((-tile_lat) + (3 if lat % 3 != 0 else 0))
    if lon < 0:
        tile_lon = -((-tile_lon) + (3 if lon % 3 != 0 else 0))

    ns = "N" if tile_lat >= 0 else "S"
    ew = "E" if tile_lon >= 0 else "W"
    return f"{ns}{abs(tile_lat):02d}{ew}{abs(tile_lon):03d}"


def tile_url(lat: float, lon: float) -> str:
    """Get the HTTPS URL for the ESA WorldCover tile covering the given coordinate."""
    tid = _tile_id(lat, lon)
    return f"{ESA_BASE_URL}/{ESA_PATH}/ESA_WorldCover_10m_2021_v200_{tid}_Map.tif"


def tiles_for_bbox(south: float, west: float, north: float, east: float) -> list[str]:
    """
    Get all tile URLs covering a bounding box.
    Returns unique list of tile URLs.
    """
    urls = set()
    # Step through bbox in 3° increments (tile size)
    lat = south
    while lat <= north:
        lon = west
        while lon <= east:
            urls.add(tile_url(lat, lon))
            lon += 3.0
        lat += 3.0
    return list(urls)

