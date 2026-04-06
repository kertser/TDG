"""
Overpass QL query templates for OSM terrain analysis.

Builds bbox-filtered queries for landuse, natural, highway, waterway, building features.
Handles rate limiting (1 req/s), retries, and bbox splitting for large areas.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
OVERPASS_TIMEOUT = 180  # seconds – generous for large areas
MAX_RETRIES = 3
RETRY_DELAY = 5.0  # seconds between retries

# Approximate bbox side length in degrees that Overpass handles well
# ~0.15° ≈ 16km at mid-latitudes.  Anything larger gets tiled.
MAX_BBOX_SPAN_DEG = 0.15


def build_terrain_query(
    south: float, west: float, north: float, east: float,
    skip_buildings: bool = False,
) -> str:
    """
    Build an Overpass QL query to fetch all terrain-relevant features in a bbox.
    Returns roads, buildings (optional), waterways, landuse, natural features.

    For very large areas set skip_buildings=True to avoid timeout – ESA
    WorldCover already provides urban classification from satellite data.
    """
    bbox = f"{south},{west},{north},{east}"
    building_part = "" if skip_buildings else f'  way["building"]({bbox});\n'
    return f"""
[out:json][timeout:{OVERPASS_TIMEOUT}][maxsize:536870912];
(
  way["highway"]({bbox});
  way["waterway"]({bbox});
{building_part}  way["landuse"]({bbox});
  way["natural"]({bbox});
  way["bridge"]({bbox});
  relation["landuse"]({bbox});
  relation["natural"]({bbox});
  relation["waterway"]({bbox});
);
out geom;
"""


async def query_overpass(query: str) -> dict:
    """
    Execute an Overpass API query with retries and exponential back-off.
    Returns the parsed JSON response.
    """
    for attempt in range(MAX_RETRIES):
        try:
            async with httpx.AsyncClient(timeout=OVERPASS_TIMEOUT + 30) as client:
                resp = await client.post(
                    OVERPASS_URL,
                    data={"data": query},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    wait = RETRY_DELAY * (attempt + 1) * 2
                    logger.warning(f"Overpass rate-limited, waiting {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                    continue
                elif resp.status_code == 504:
                    wait = RETRY_DELAY * (attempt + 1)
                    logger.warning(f"Overpass 504 timeout, waiting {wait}s (attempt {attempt + 1})")
                    await asyncio.sleep(wait)
                    continue
                else:
                    logger.error(f"Overpass error {resp.status_code}: {resp.text[:200]}")
                    await asyncio.sleep(RETRY_DELAY)
        except httpx.TimeoutException:
            logger.warning(f"Overpass HTTP timeout, attempt {attempt + 1}")
            await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        except Exception as e:
            logger.error(f"Overpass request failed: {e}")
            await asyncio.sleep(RETRY_DELAY)

    logger.error("Overpass query failed after all retries")
    return {"elements": []}


async def query_overpass_tiled(
    south: float, west: float, north: float, east: float,
) -> dict:
    """
    If the bbox is too large, split it into tiles and query each separately.
    Merges all element lists together.  Skips buildings for large areas.
    """
    lat_span = north - south
    lon_span = east - west

    # Decide whether we need to tile
    need_tile = lat_span > MAX_BBOX_SPAN_DEG or lon_span > MAX_BBOX_SPAN_DEG
    skip_buildings = lat_span > MAX_BBOX_SPAN_DEG * 0.7 or lon_span > MAX_BBOX_SPAN_DEG * 0.7

    if not need_tile:
        query = build_terrain_query(south, west, north, east, skip_buildings=False)
        return await query_overpass(query)

    # Split into tiles
    n_lat = max(1, int(lat_span / MAX_BBOX_SPAN_DEG) + 1)
    n_lon = max(1, int(lon_span / MAX_BBOX_SPAN_DEG) + 1)
    d_lat = lat_span / n_lat
    d_lon = lon_span / n_lon

    logger.info(f"Overpass bbox too large ({lat_span:.3f}°×{lon_span:.3f}°), "
                f"tiling into {n_lat}×{n_lon}={n_lat*n_lon} tiles"
                f"{' (buildings skipped)' if skip_buildings else ''}")

    all_elements: list[dict] = []
    seen_ids: set[int] = set()

    for i_lat in range(n_lat):
        for i_lon in range(n_lon):
            t_south = south + i_lat * d_lat
            t_north = south + (i_lat + 1) * d_lat
            t_west = west + i_lon * d_lon
            t_east = west + (i_lon + 1) * d_lon

            query = build_terrain_query(
                t_south, t_west, t_north, t_east,
                skip_buildings=skip_buildings,
            )
            data = await query_overpass(query)
            for elem in data.get("elements", []):
                eid = elem.get("id", 0)
                if eid not in seen_ids:
                    seen_ids.add(eid)
                    all_elements.append(elem)

            # Rate limit: 1 req/s for Overpass
            await asyncio.sleep(1.0)

    logger.info(f"Overpass tiled query: {len(all_elements)} unique elements from {n_lat*n_lon} tiles")
    return {"elements": all_elements}
