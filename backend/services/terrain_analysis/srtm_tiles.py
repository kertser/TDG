"""
Copernicus DEM GLO-30 tile index — maps lat/lon to COG tile URLs on AWS S3.

Copernicus DEM 30m tiles are 1°×1° Cloud-Optimized GeoTIFF files hosted at:
  s3://copernicus-dem-30m/Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM/
    Copernicus_DSM_COG_10_{NS}{lat:02d}_00_{EW}{lon:03d}_00_DEM.tif

Tile naming convention: the ID encodes the **SW corner** of each 1°×1° cell
using the *upper* latitude edge. So a tile covering lat 49→50 is labelled N50,
and a tile covering lat -1→0 is labelled S01.

HTTPS access via CloudFront:
  https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com/...

No authentication required.
"""

from __future__ import annotations

import math


# AWS S3 HTTPS endpoint for Copernicus DEM 30m
COP_BASE_URL = "https://copernicus-dem-30m.s3.eu-central-1.amazonaws.com"


def _tile_id(lat: float, lon: float) -> str:
    """
    Compute the Copernicus DEM tile ID for a coordinate.

    Each tile covers 1°×1°.  The tile ID encodes the **SW corner**:
      - Latitude label  = floor(lat), prefixed N or S
      - Longitude label = floor(lon), prefixed E or W

    Examples:
      (49.5, 4.3)  → N49_00_E004_00  (tile covers 49°–50° N, 4°–5° E)
      (0.5, -3.7)  → N00_00_W004_00
      (-0.5, 10.2) → S01_00_E010_00
    """
    # Floor to get SW corner
    lat_floor = math.floor(lat)
    lon_floor = math.floor(lon)

    if lat_floor >= 0:
        ns = "N"
        lat_val = lat_floor
    else:
        ns = "S"
        lat_val = abs(lat_floor)

    if lon_floor >= 0:
        ew = "E"
        lon_val = lon_floor
    else:
        ew = "W"
        lon_val = abs(lon_floor)

    return f"{ns}{lat_val:02d}_00_{ew}{lon_val:03d}_00"


def tile_url(lat: float, lon: float) -> str:
    """Get the HTTPS URL for the Copernicus DEM tile covering (lat, lon)."""
    tid = _tile_id(lat, lon)
    folder = f"Copernicus_DSM_COG_10_{tid}_DEM"
    filename = f"Copernicus_DSM_COG_10_{tid}_DEM.tif"
    return f"{COP_BASE_URL}/{folder}/{filename}"


def tiles_for_bbox(
    south: float, west: float, north: float, east: float,
) -> list[str]:
    """
    Get all unique tile URLs covering a bounding box.

    Steps through the bbox in 1° increments (tile size) to catch every
    tile that might be needed, including edge overlaps.
    """
    urls: set[str] = set()
    # Iterate from south to north, west to east in <1° steps to be safe
    lat = math.floor(south)
    while lat <= math.ceil(north):
        lon = math.floor(west)
        while lon <= math.ceil(east):
            urls.add(tile_url(lat + 0.5, lon + 0.5))
            lon += 1
        lat += 1
    return list(urls)


